"""One thermostat as Home Assistant sees it: local first, cloud as fallback.

Reads come from HomeKit while the local connection is up; the cloud fills in
only while it's down, so the two sources never fight. Writes go to HomeKit
first and fall back to the cloud. Late local writes are reconciled with newer
commands so an old request cannot remain the thermostat's final state.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from datetime import date, datetime
from typing import TYPE_CHECKING, Any

from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util
from mysa_client.cloud import (
    MODEL_NAMES,
    AcFan,
    ButtonLock,
    Capabilities,
    DeviceRecord,
    DeviceState,
    FirmwareInfo,
    HeaterType,
    Mode,
    ModelFamily,
    MysaError,
    Setpoint,
    SetpointError,
    SetpointKind,
    SetpointRange,
    SetpointRules,
    Swing,
    TemperatureOffset,
    model_family,
)
from mysa_client.cloud.commands import CloudCommand
from mysa_client.cloud.const import (
    EXPERIMENTAL_CLOUD_FAMILIES,
    SWING_VERTICAL,
)
from mysa_client.cloud.device import (
    HEATING_FAMILIES,
    basic_modes,
    hvac_fan_operation,
)
from mysa_client.cloud.device_commands import (
    brightness_command,
    climate_command,
    hvac_fan_command,
    hvac_fan_speed_command,
)
from mysa_client.cloud.setpoints import (
    fahrenheit_label,
    round_half_up,
    round_to_step,
    to_celsius,
)
from mysa_client.homekit import Field, LocalMode
from mysa_client.runtime.routing import RouteRequest
from mysa_client.runtime.state import DeviceView

from .account import cloud_account_status
from .cloud_coordinator import MysaCloudCoordinator, translated_error
from .command_router import CommandRouter
from .const import (
    CLOUD_TELEMETRY_FUTURE_TOLERANCE,
    CONF_CLOUD_RUNTIME,
    CONF_DEVICE_ID,
    CONF_HEATER_WATTS,
    CONF_MODEL,
    CONNECTION_CLOUD,
    CONNECTION_LOCAL,
    CONNECTION_OFFLINE,
)
from .event_history import EventHistory, error_category
from .local_adapter import (
    AMBIENT_LIGHT,
    DISPLAY_UNITS,
    LOCAL_ERRORS,
    TARGET_STATE,
    TARGET_TEMPERATURE,
    LocalThermostat,
)
from .temperature import Unit, setpoint_error

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigSubentry

    from .mqtt_coordinator import MysaMqttCoordinator

# Translate the local protocol's semantic modes into Mysa's device modes.
LOCAL_MODE_TO_MODE = {
    LocalMode.OFF: Mode.OFF,
    LocalMode.HEAT: Mode.HEAT,
    LocalMode.COOL: Mode.COOL,
    LocalMode.AUTO: Mode.AUTO,
}
MODE_TO_LOCAL_MODE = {mode: local for local, mode in LOCAL_MODE_TO_MODE.items()}

ACTION_OFF = "off"
ACTION_IDLE = "idle"
ACTION_HEATING = "heating"
ACTION_COOLING = "cooling"


def _same_inputs(old: tuple[Any, ...], new: tuple[Any, ...]) -> bool:
    """Capabilities and record by identity (replaced on each fetch), else equality."""
    return all(
        a is b if index in (0, 4) else a == b
        for index, (a, b) in enumerate(zip(old, new, strict=True))
    )


class Unchanged(Exception):  # noqa: N818 - a signal, not an error
    """A whole-°F request for the label already shown: nothing to send.

    ``setpoint`` is the current target, for callers that report what is set.
    """

    def __init__(self, setpoint: Setpoint | None) -> None:
        """Keep the unchanged target."""
        super().__init__()
        self.setpoint = setpoint


def repicks_label(value: float, unit: Unit, current: float | None) -> bool:
    """Whether a °F request is exactly the whole °F the current target shows.

    22.5 °C shows as 73 °F; 73 °F itself would round to 23 °C, so picking the
    label you already see would change the setpoint (owner, 2026-11-15).
    """
    return unit == "F" and current is not None and value == fahrenheit_label(current)


def tap_value(
    limits: SetpointRange, requested: float, unit: Unit, current: float | None
) -> tuple[float, Unit]:
    """Make a whole-°F tap on the card always move the setpoint.

    With a step coarser than 1 °F (a 1 °C cool step, say), the next whole °F
    can round straight back to the current setpoint: + from 73 °F (23 °C)
    asks for 74 °F, which is 23.3 °C and rounds to 23 °C again. When the
    request rounds to the current setpoint but isn't its label, move one step
    towards the request instead (+ from 73 goes to 24 °C, 75 °F). A value far
    from the current one, or any value with a 0.5 °C step, is unaffected.
    Returns the value to normalise and its unit.
    """
    if unit != "F" or current is None:
        return requested, unit
    label = fahrenheit_label(current)
    rounded = round_to_step(to_celsius(requested, "F"), limits.step)
    if rounded != current or round_half_up(requested) == label:
        return requested, unit
    direction = 1 if requested > label else -1
    return round(current + direction * limits.step, 4), "C"


class RuntimeTracker:
    """Heating on-time since local midnight, derived from the relay state."""

    def __init__(self) -> None:
        """Start at zero for today."""
        self.day: date = dt_util.now().date()
        self.seconds = 0.0
        self._since: datetime | None = None

    def restore(self, day: date, seconds: float) -> None:
        """Continue today's count after a restart."""
        if day == dt_util.now().date():
            self.day = day
            self.seconds = max(self.seconds, seconds)

    def raise_to(self, seconds: float) -> None:
        """Count at least `seconds` today (the thermostat's own, fuller figure)."""
        shortfall = seconds - self.total()
        if shortfall > 0:
            self.seconds += shortfall

    def _roll(self, now: datetime) -> None:
        if now.date() == self.day:
            return
        # A heating cycle that runs past midnight counts from midnight today.
        if self._since is not None:
            self._since = now.replace(hour=0, minute=0, second=0, microsecond=0)
        self.day = now.date()
        self.seconds = 0.0

    def update(self, heating: bool | None) -> None:
        """Record the relay state as of now (None = unknown, treated as off)."""
        now = dt_util.now()
        self._roll(now)
        if self._since is not None:
            self.seconds += max(0.0, (now - self._since).total_seconds())
            self._since = None
        if heating:
            self._since = now

    def total(self) -> float:
        """Seconds today, including a heating cycle that is still running."""
        now = dt_util.now()
        self._roll(now)
        running = (now - self._since).total_seconds() if self._since else 0.0
        return self.seconds + max(0.0, running)


class Thermostat:
    """Routes reads and writes for one Mysa between HomeKit and the cloud."""

    def __init__(
        self,
        hass: HomeAssistant,
        subentry: ConfigSubentry,
        local: LocalThermostat | None,
        cloud_getter: Callable[[], MysaCloudCoordinator | None],
        *,
        history: EventHistory | None = None,
        mqtt_getter: Callable[[], MysaMqttCoordinator | None] | None = None,
    ) -> None:
        """Wire up one thermostat and its optional MQTT telemetry source."""
        self.history = history if history is not None else EventHistory()
        self._last_local_available: bool | None = None
        self.hass = hass
        self.subentry_id = subentry.subentry_id
        self.device_id: str = subentry.data[CONF_DEVICE_ID]
        self.name = subentry.title
        self.local = local
        self._cloud_getter = cloud_getter
        self._mqtt_getter = mqtt_getter
        self.heater_watts: float = float(subentry.data.get(CONF_HEATER_WATTS) or 0)
        self.use_cloud_runtime: bool = subentry.data.get(CONF_CLOUD_RUNTIME, True)
        self._model: str | None = subentry.data.get(CONF_MODEL)
        self._listeners: set[CALLBACK_TYPE] = set()
        self.runtime = RuntimeTracker()
        self._in_fallback = False
        self._rules: tuple[tuple[Any, ...], SetpointRules] | None = None
        self.commands = CommandRouter(
            hass,
            device_id=self.device_id,
            name=self.name,
            local=local,
            cloud_getter=cloud_getter,
            notify=self.notify,
            history=self.history,
        )
        if local is not None:
            local.add_listener(self.notify)

    # ---- listeners -----------------------------------------------------

    @callback
    def add_listener(self, listener: CALLBACK_TYPE) -> CALLBACK_TYPE:
        """Call `listener` after any local or cloud change."""
        self._listeners.add(listener)
        return lambda: self._listeners.discard(listener)

    @callback
    def notify(self) -> None:
        """Tell entities something changed."""
        if (
            self.local is not None
            and self.local.available != self._last_local_available
        ):
            self._last_local_available = self.local.available
            self.history.record(
                "connection",
                noisy=True,
                device_id=self.device_id,
                transport="homekit",
                status="recovery" if self.local.available else "failure",
            )
        self._update_fallback()
        # Cloud dutyCycle is an interval fraction, not an instantaneous relay
        # state. During fallback only the device's measured daily usage counts.
        self.runtime.update(self.heating if self.local_ok else None)
        self.commands.schedule_reconciliation()
        for listener in list(self._listeners):
            listener()

    @callback
    def notify_entities(self) -> None:
        """Ask entities to write their state again, without other side effects."""
        for listener in list(self._listeners):
            listener()

    @callback
    def _update_fallback(self) -> None:
        """Let the cloud know when this thermostat starts or stops relying on it."""
        cloud = self.cloud
        if cloud is None or self.local is None:
            return
        in_fallback = not self.local_ok
        if in_fallback != self._in_fallback:
            self._in_fallback = in_fallback
            cloud.set_fallback(self.device_id, in_fallback)

    # ---- heating runtime ----------------------------------------------

    @property
    def cloud_runtime_today(self) -> float | None:
        """Today's heating seconds as measured by the thermostat (cloud)."""
        cloud = self.cloud
        if not self.use_cloud_runtime or cloud is None or cloud.data is None:
            return None
        usage = cloud.data.usage_today.get(self.device_id)
        if usage is None:
            return None
        midnight, seconds = usage
        if midnight.date() != dt_util.now().date():
            return None
        return seconds

    @property
    def runtime_today(self) -> float:
        """Seconds of heating today; never goes down within a day.

        The larger of the local count and the thermostat's own figure (cloud).
        The cloud figure lags by up to ~20 min but also covers time Home
        Assistant was down. Switching between them could otherwise lower the
        total, which statistics read as a meter reset and count twice. When the
        cloud is ahead, the local count is raised to match, so losing the cloud
        later can't lower it either.
        """
        cloud_value = self.cloud_runtime_today
        if cloud_value is not None:
            self.runtime.raise_to(cloud_value)
        return self.runtime.total()

    @property
    def runtime_source(self) -> str:
        """Cloud while the thermostat's own figure feeds runtime_today, else local."""
        return (
            CONNECTION_CLOUD
            if self.cloud_runtime_today is not None
            else CONNECTION_LOCAL
        )

    # ---- sources -------------------------------------------------------

    @property
    def cloud(self) -> MysaCloudCoordinator | None:
        """The cloud coordinator, or None when the cloud is off or not set up."""
        return self._cloud_getter()

    @property
    def mqtt(self) -> MysaMqttCoordinator | None:
        """The MQTT coordinator, or None when MQTT telemetry is unavailable."""
        return self._mqtt_getter() if self._mqtt_getter is not None else None

    def _view(self) -> DeviceView:
        """Compose the library's semantic view using HA's selected sources."""
        cloud, mqtt = self.cloud, self.mqtt
        return DeviceView(
            state=self.cloud_state,
            record=self.cloud_record,
            family=self.family,
            local={str(k): v for k, v in self.local.values.items()}
            if self.local
            else {},
            local_ok=self.local_ok,
            cloud_ok=self.cloud_ok,
            cloud_fresh=lambda name: bool(
                cloud and cloud.measurement_fresh(self.device_id, name)
            ),
            mqtt={
                name: mqtt.value(self.device_id, name) if mqtt else None
                for name in ("humidity", "duty_cycle")
            },
        )

    def has_mqtt_value(self, name: str) -> bool:
        """Whether MQTT has reported a valid value for entity discovery."""
        mqtt = self.mqtt
        return mqtt.has_value(self.device_id, name) if mqtt is not None else False

    @property
    def cloud_state(self) -> DeviceState | None:
        """Latest cloud state, if any."""
        cloud = self.cloud
        return cloud.state(self.device_id) if cloud else None

    @property
    def cloud_record(self) -> DeviceRecord | None:
        """Cloud settings record, if any."""
        cloud = self.cloud
        return cloud.record(self.device_id) if cloud else None

    @property
    def setpoint_min(self) -> float | None:
        """Lowest confirmed setpoint, falling back to the device settings record."""
        state = self.cloud_state
        if state is not None and state.setpoint_min is not None:
            return state.setpoint_min
        record = self.cloud_record
        return record.setpoint_min if record is not None else None

    @property
    def setpoint_max(self) -> float | None:
        """Highest confirmed setpoint, falling back to the device settings record."""
        state = self.cloud_state
        if state is not None and state.setpoint_max is not None:
            return state.setpoint_max
        record = self.cloud_record
        return record.setpoint_max if record is not None else None

    @property
    def capabilities(self) -> Capabilities | None:
        """Cloud capability document, if any."""
        cloud = self.cloud
        return cloud.capabilities(self.device_id) if cloud else None

    @property
    def firmware_info(self) -> FirmwareInfo | None:
        """Cloud firmware check result, if any."""
        cloud = self.cloud
        if cloud is None or cloud.data is None:
            return None
        return cloud.data.firmware.get(self.device_id)

    @property
    def hold(self) -> dict[str, Any] | None:
        """The active schedule hold, if the cloud reported one."""
        cloud = self.cloud
        if cloud is None or cloud.data is None:
            return None
        return cloud.data.holds.get(self.device_id)

    @property
    def account_status(self) -> str | None:
        """on_account, not_on_account, or None while unknown (cloud off)."""
        return cloud_account_status(self.cloud, self.device_id)

    @property
    def has_local(self) -> bool:
        """True if this thermostat is paired over HomeKit."""
        return self.local is not None

    @property
    def local_ok(self) -> bool:
        """True while the HomeKit connection works."""
        return self.commands.local_ok

    @property
    def cloud_ok(self) -> bool:
        """True while the cloud has fresh data and sees the thermostat online."""
        cloud = self.cloud
        if cloud is None or not cloud.last_update_success:
            return False
        state = cloud.state(self.device_id)
        if state is None or state.connected is not True or state.reading_time is None:
            return False
        age = dt_util.utcnow() - state.reading_time
        return (
            -CLOUD_TELEMETRY_FUTURE_TOLERANCE
            <= age
            <= cloud.measurement_max_age(self.device_id)
        )

    @property
    def cloud_connected(self) -> bool:
        """True when a successful cloud poll reports the thermostat online.

        This supports cloud settings whose confirmed values are available even
        when the separate telemetry reading has not advanced recently.
        """
        cloud = self.cloud
        if cloud is None or not cloud.last_update_success:
            return False
        state = cloud.state(self.device_id)
        return state is not None and state.connected is True

    @property
    def connection_path(self) -> str:
        """Which path serves the thermostat right now."""
        if self.local_ok:
            return CONNECTION_LOCAL
        if self.cloud_ok:
            return CONNECTION_CLOUD
        return CONNECTION_OFFLINE

    @property
    def available(self) -> bool:
        """True if either path works."""
        return self.connection_path != CONNECTION_OFFLINE

    # ---- device info ---------------------------------------------------

    @property
    def model(self) -> str | None:
        """Mysa model string, e.g. BB-V2-0-L."""
        if self.local is not None and self.local.info.model:
            return self.local.info.model
        state = self.cloud_state
        if state is not None and state.model:
            return state.model
        return self._model

    @property
    def family(self) -> ModelFamily | None:
        """Model family from the account record or device identity."""
        record = self.cloud_record
        return model_family(record.model if record and record.model else self.model)

    @property
    def basic_cloud_only(self) -> bool:
        """New-model support intentionally excludes legacy cloud extras."""
        return self.family in EXPERIMENTAL_CLOUD_FAMILIES

    @property
    def is_climate_sensor(self) -> bool:
        """Climate Sensor is a measurement device, never a heater."""
        return self.family == ModelFamily.CLIMATE_SENSOR

    @property
    def is_heating_model(self) -> bool:
        """Whether this model family can expose a heating duty cycle."""
        return self.family in HEATING_FAMILIES

    @property
    def is_hvac(self) -> bool:
        """Central HVAC uses native capabilities and dual auto targets."""
        return self.family == ModelFamily.CENTRAL_HVAC

    @property
    def model_name(self) -> str | None:
        """Friendly model name."""
        model = self.model
        return MODEL_NAMES.get(model, model) if model else None

    @property
    def cloud_name(self) -> str | None:
        """The thermostat's name in the Mysa app, if it's on the account."""
        record = self.cloud_record
        return record.name if record else None

    @property
    def device_model(self) -> str:
        """Model line for the device registry: model, Mysa ID, Mysa app name.

        Home Assistant's device lists and the device page show only a device's
        name and model, so the thermostat's Mysa ID (and its name in the Mysa
        app, when that differs) go here to be seen at a glance.
        """
        parts = [self.model_name or "Mysa", f"Mysa ID {self.device_id}"]
        if self.basic_cloud_only:
            parts.append("Experimental cloud support")
        cloud_name = self.cloud_name
        if cloud_name and cloud_name.casefold() != self.name.casefold():
            parts.append(f"Mysa app: {cloud_name}")
        return " · ".join(parts)

    @property
    def firmware(self) -> str | None:
        """Full firmware version (the cloud has all four parts)."""
        state = self.cloud_state
        if state is not None and state.firmware:
            return state.firmware
        if self.local is not None:
            return self.local.info.firmware
        return None

    @property
    def serial(self) -> str | None:
        """Serial number (HomeKit only; the cloud doesn't have it)."""
        return self.local.info.serial if self.local is not None else None

    @property
    def mac(self) -> str:
        """Wi-Fi MAC as aa:bb:cc:dd:ee:ff."""
        dev = self.device_id
        return ":".join(dev[i : i + 2] for i in range(0, 12, 2))

    # ---- climate values ------------------------------------------------

    @property
    def current_temperature(self) -> float | None:
        """Room temperature in °C."""
        return self._view().temperature

    @property
    def has_light_sensor(self) -> bool:
        """True if the paired HomeKit accessory advertises a light sensor."""
        return (
            AMBIENT_LIGHT is not None
            and self.local is not None
            and self.local.supports(AMBIENT_LIGHT)
        )

    @property
    def illuminance(self) -> float | None:
        """Ambient light in lux. Only HomeKit reports it; no cloud fallback."""
        local = self.local
        if (
            AMBIENT_LIGHT is None
            or local is None
            or not local.available
            or AMBIENT_LIGHT in local.failed_fields
        ):
            return None
        value = local.values.get(AMBIENT_LIGHT)
        return value if isinstance(value, float) else None

    @property
    def humidity(self) -> float | None:
        """Room humidity in %, preferring fresh MQTT over fresh cloud data."""
        return self._view().measurement("humidity")[0]

    @property
    def humidity_source(self) -> str | None:
        """Source of the current humidity value, when one is fresh."""
        return self._view().measurement("humidity")[1]

    @property
    def duty_cycle(self) -> float | None:
        """Numeric heating fraction, preferring fresh MQTT over cloud telemetry."""
        return self._view().measurement("duty_cycle")[0]

    @property
    def duty_cycle_source(self) -> str | None:
        """Source of the current numeric duty-cycle value, when one is fresh."""
        return self._view().measurement("duty_cycle")[1]

    @property
    def mode(self) -> Mode | None:
        """Current mode."""
        return self._view().mode

    @property
    def target_temperature(self) -> float | None:
        """Setpoint in °C for the current mode."""
        return self._view().target

    @property
    def action(self) -> str | None:
        """off, idle, heating or cooling (the reported device activity)."""
        return self._view().action

    @property
    def heating(self) -> bool | None:
        """True while the heater is on."""
        action = self.action
        if action is None:
            return None
        return action == ACTION_HEATING

    @property
    def heat_fraction(self) -> float | None:
        """Fresh MQTT duty cycle, else local relay state or fresh cloud average."""
        return self._view().heat_fraction

    @property
    def display_unit(self) -> str | None:
        """C or F, as shown on the thermostat's own screen."""
        return self._view().display_unit

    @property
    def supported_modes(self) -> list[Mode]:
        """Modes this model supports, from the capability data or HomeKit."""
        caps = self.capabilities
        if self.basic_cloud_only:
            return basic_modes(self.family, caps, self.cloud_state)
        if caps is not None and caps.modes:
            return [m for m in caps.modes if m in SUPPORTED_MODES]
        if self.local is not None and self.local.supports(TARGET_STATE):
            return [LOCAL_MODE_TO_MODE[m] for m in self.local.supported_modes]
        return [Mode.OFF, Mode.HEAT]

    def _rules_inputs(self) -> tuple[Any, ...]:
        return (
            self.capabilities,
            self.local.temperature_range if self.local else None,
            self.setpoint_min,
            self.setpoint_max,
            self.cloud_record,
        )

    @property
    def rules(self) -> SetpointRules:
        """The one rules object every written temperature goes through.

        Rebuilt only when an input changes: the cloud capabilities, the
        HomeKit range, the owner's confirmed limits or the settings record
        (which carries a Mini-Split AC's cool range).
        """
        inputs = self._rules_inputs()
        cached = self._rules
        if cached is None or not _same_inputs(cached[0], inputs):
            caps, local_range, setpoint_min, setpoint_max, record = inputs
            rules = SetpointRules(
                caps,
                local_range,
                setpoint_min=setpoint_min,
                setpoint_max=setpoint_max,
                record=record,
            )
            self._rules = cached = (inputs, rules)
        return cached[1]

    @property
    def setpoint_kinds(self) -> tuple[SetpointKind, ...]:
        """The setpoint ranges the climate entity's bounds have to cover.

        HA validates a target before applying a mode change in the same call,
        so Central HVAC exposes the envelope of all its ranges; the rules
        enforce each mode's own limits when writing. Other models show the
        range of the mode they're in.
        """
        if self.is_hvac and self.capabilities is not None:
            return tuple(SetpointKind)
        return (SetpointKind.COOL if self.mode == Mode.COOL else SetpointKind.HEAT,)

    @property
    def setpoint_range(self) -> tuple[float, float, float]:
        """(min, max, step) in °C for the climate entity, from the rules."""
        limits = self.rules.envelope(self.setpoint_kinds)
        assert limits.minimum is not None
        assert limits.maximum is not None
        return limits.minimum, limits.maximum, limits.step

    @property
    def fahrenheit_setpoint_range(self) -> tuple[int, int]:
        """Lowest and highest whole °F the climate entity accepts."""
        return self.rules.fahrenheit_envelope(self.setpoint_kinds)

    def _setpoint(
        self,
        mode: Mode | None,
        value: float | Setpoint,
        *,
        unit: Unit,
        strict: bool,
        current: float | None = None,
        step: bool = False,
    ) -> Setpoint:
        """One target for ``mode``, rounded and checked; errors in ``unit``.

        ``current`` is the target now shown (°C); re-picking its whole °F
        raises ``Unchanged``. ``step`` says the value is a + or - on the card,
        which always moves the setpoint (``tap_value``); without it the value
        goes to its nearest setpoint, so repeating a request settles.
        """
        if isinstance(value, Setpoint):
            return value
        rules = self.rules
        if repicks_label(float(value), unit, current):
            assert current is not None
            raise Unchanged(rules.setpoint(mode, current, strict=False))
        value, unit_in = float(value), unit
        if step:
            value, unit_in = tap_value(rules.range_for(mode), value, unit, current)
        try:
            return rules.setpoint(mode, value, unit=unit_in, strict=strict)
        except SetpointError as err:
            raise setpoint_error(err, unit) from err

    def _heat_cool(
        self,
        low: float,
        high: float,
        *,
        unit: Unit,
        strict: bool,
        current: tuple[float | None, float | None] | None = None,
        step: bool = False,
    ) -> tuple[Setpoint, Setpoint]:
        """A heat/cool pair; the gap is always checked, whatever ``strict`` says.

        With ``current``, each side is treated like a single target: a re-picked
        label keeps that side as it is, and if both are kept nothing is sent
        (``Unchanged``).
        """
        rules = self.rules
        if current is not None and unit == "F":
            # Each side on its own; both then go to the rules in °C.
            sides: list[float] = []
            kept = 0
            for kind, value, now in (
                (SetpointKind.AUTO_HEAT, low, current[0]),
                (SetpointKind.AUTO_COOL, high, current[1]),
            ):
                if repicks_label(float(value), unit, now):
                    assert now is not None
                    sides.append(now)
                    kept += 1
                else:
                    side: tuple[float, Unit] = (float(value), unit)
                    if step:
                        side = tap_value(rules.range_for(kind), *side, now)
                    sides.append(to_celsius(*side))
            if kept == 2:
                raise Unchanged(None)
            low, high = sides
            unit_in: Unit = "C"
        else:
            unit_in = unit
        try:
            return rules.heat_cool(float(low), float(high), unit=unit_in, strict=strict)
        except SetpointError as err:
            raise setpoint_error(err, unit) from err

    @property
    def hvac_fan_modes(self) -> list[str]:
        """Native fan operation, distinct from an AC's IR fan speeds."""
        caps = self.capabilities
        return list(caps.hvac_fan_operations) if self.is_hvac and caps else []

    @property
    def hvac_fan_mode(self) -> str | None:
        """Confirmed fan operation; a configured circulation cycle is runtime."""
        return hvac_fan_operation(self.cloud_state)

    async def async_set_basic_temperature(
        self,
        *,
        mode: Mode | None = None,
        temperature: float | Setpoint | None = None,
        low: float | None = None,
        high: float | None = None,
        unit: Unit = "C",
        strict: bool = True,
        tap: bool = False,
        step: bool = False,
    ) -> dict[str, Setpoint]:
        """Set mode and associated targets as one validated library operation.

        Returns the written targets by name (temperature, low, high), so the
        caller can tell the owner about any value the rules clamped. With
        ``tap``, re-picking a target's shown whole °F leaves it as it is;
        ``step`` (a + or - on the card) also makes a whole-°F step always
        move the setpoint (``tap_value``).
        """
        written: dict[str, Setpoint] = {}
        if mode is None and temperature is None and low is None and high is None:
            return written

        def command(cloud: MysaCloudCoordinator) -> CloudCommand:
            selected_mode = cloud.command_value(self.device_id, "mode", self.mode)
            target_mode = mode if mode is not None else selected_mode
            written.clear()
            # No current label to step from or keep when the mode changes.
            same_mode = mode is None or mode == selected_mode
            state = self.cloud_state if tap and same_mode else None
            if temperature is not None:
                written["temperature"] = self._setpoint(
                    target_mode,
                    temperature,
                    unit=unit,
                    strict=strict,
                    current=state.setpoint_for(target_mode) if state else None,
                    step=step,
                )
            if low is not None and high is not None:
                written["low"], written["high"] = self._heat_cool(
                    low,
                    high,
                    unit=unit,
                    strict=strict,
                    current=(
                        (state.auto_heat_setpoint, state.auto_cool_setpoint)
                        if state and state.mode == Mode.AUTO
                        else None
                    ),
                    step=step,
                )
            elif low is not None or high is not None:
                raise ValueError("Supply both low and high setpoints in heat/cool mode")
            return climate_command(
                self.family,
                selected_mode,
                mode=mode,
                temperature=written.get("temperature"),
                low=written.get("low"),
                high=written.get("high"),
            )

        try:
            await self._async_prepared_cloud_command(command)
        except Unchanged as unchanged:
            # The shown label was picked again: nothing was sent.
            if unchanged.setpoint is None:
                return {}
            return {"temperature": unchanged.setpoint}
        return written

    @property
    def hvac_fan_speeds(self) -> tuple[int, ...]:
        """Speeds explicitly advertised in the current Central HVAC mode."""
        caps = self.capabilities
        return caps.hvac_fan_speeds(self.mode) if self.is_hvac and caps else ()

    async def async_set_hvac_fan_speed(self, speed: int) -> None:
        """Change native fan speed without AC enum conversion."""
        await self._async_prepared_cloud_command(
            lambda _cloud: hvac_fan_speed_command(speed)
        )

    async def async_set_hvac_fan_mode(self, mode: str) -> None:
        """Set native fan operation through the client command API."""
        if mode not in self.hvac_fan_modes:
            raise HomeAssistantError("Unsupported fan operation")
        await self._async_prepared_cloud_command(lambda _cloud: hvac_fan_command(mode))

    # ---- Mini-Split AC fan and swing (cloud only) ------------------------

    @property
    def _ac_record(self) -> DeviceRecord | None:
        """The cloud record when this is a Mini-Split AC."""
        record = self.cloud_record
        if record is None:
            return None
        family = model_family(record.model or self.model)
        return record if family is ModelFamily.AC else None

    @property
    def fan_speeds(self) -> tuple[AcFan, ...]:
        """Fan speeds the AC's remote offers in the current mode.

        None while off: Mysa sends the AC a full IR command, so changing the
        fan of an AC that's off could switch it on. Mysa's app hides it too.
        """
        record, mode = self._ac_record, self.mode
        if record is None or mode in (None, Mode.OFF):
            return ()
        return record.ac_fan_speeds(mode)

    @property
    def fan_speed(self) -> AcFan | None:
        """The AC's fan speed, as last confirmed by the cloud."""
        state = self.cloud_state
        if self._ac_record is None or state is None or state.fan_mode is None:
            return None
        return state.ac_fan

    def swing_options(self, direction: str) -> tuple[Swing, ...]:
        """Swing settings the AC's remote offers in the current mode."""
        record, mode = self._ac_record, self.mode
        if record is None or mode in (None, Mode.OFF):
            return ()
        return record.ac_swing(mode, direction)

    def swing(self, direction: str) -> Swing | None:
        """The AC's swing setting in one direction, as last confirmed."""
        state = self.cloud_state
        if self._ac_record is None or state is None:
            return None
        value = (
            state.vertical_swing
            if direction == SWING_VERTICAL
            else state.horizontal_swing
        )
        return Swing(value) if value is not None and value in Swing else None

    # ---- writes --------------------------------------------------------

    @callback
    def stop(self) -> None:
        """Stop scheduling command reconciliation during unload."""
        self.commands.stop()

    async def async_stop(self) -> None:
        """Drain commands and close the local connection."""
        await self.commands.async_stop()

    async def async_set_temperature(
        self,
        value: float | Setpoint,
        *,
        unit: Unit = "C",
        strict: bool = True,
        tap: bool = False,
        step: bool = False,
    ) -> Setpoint:
        """Change the setpoint using the latest serialized operating mode.

        A plain number in ``unit`` goes through the rules for the mode the
        write is made in; ``strict=False`` clamps instead of refusing. With
        ``tap``, re-picking the shown whole °F sends nothing
        (``repicks_label``); ``step`` (a + or - on the card) also makes a
        whole-°F step always move (``tap_value``).
        Returns what was written.
        """
        if self.basic_cloud_only:
            written = await self.async_set_basic_temperature(
                temperature=value, unit=unit, strict=strict, tap=tap, step=step
            )
            return written["temperature"]
        result: list[Setpoint] = []

        def prepare() -> RouteRequest[Field, Any, MysaCloudCoordinator, DeviceState]:
            mode = (
                self.cloud.command_value(self.device_id, "mode", self.mode)
                if self.cloud
                else self.mode
            )
            # Step from the shown target only when the mode isn't changing.
            current = self.target_temperature if tap and mode == self.mode else None
            setpoint = self._setpoint(
                mode, value, unit=unit, strict=strict, current=current, step=step
            )
            result[:] = [setpoint]
            celsius = setpoint.celsius
            return RouteRequest(
                # The Setpoint itself: HomeKit writes take only ruled values.
                local_values={TARGET_TEMPERATURE: setpoint},
                cloud_write=lambda cloud: cloud.async_set_temperature(
                    self.device_id, mode or Mode.HEAT, setpoint
                ),
                cloud_applied=lambda state: state.setpoint_for(mode) == celsius,
            )

        try:
            await self.commands.async_route_prepared(prepare)
        except Unchanged as unchanged:
            # The shown label was picked again: nothing was sent.
            kept = unchanged.setpoint
        else:
            return result[0]
        assert kept is not None
        return kept

    async def async_set_mode(self, mode: Mode) -> None:
        """Change the mode through an available transport."""
        if self.basic_cloud_only:
            await self.async_set_basic_temperature(mode=mode)
            return
        local_mode = MODE_TO_LOCAL_MODE.get(mode)

        async def _cloud(cloud: MysaCloudCoordinator) -> None:
            await cloud.async_set_mode(self.device_id, mode)

        await self.commands.async_route(
            local_values={TARGET_STATE: local_mode} if local_mode is not None else None,
            cloud_write=_cloud,
            cloud_applied=lambda state: state.mode == mode,
            intent={TARGET_STATE: mode.name.lower()},
        )

    async def async_set_fan_speed(self, speed: AcFan) -> None:
        """Change the AC fan with its latest serialized operating mode."""

        def prepare() -> RouteRequest[Field, Any, MysaCloudCoordinator, DeviceState]:
            mode = (
                self.cloud.command_value(self.device_id, "mode", self.mode)
                if self.cloud
                else self.mode
            )
            intent: dict[str, Any] = {"fan_speed": speed}
            if mode is not None:
                intent[TARGET_STATE] = mode.name.lower()
            return RouteRequest(
                local_values=None,
                cloud_write=lambda cloud: cloud.async_set_fan_speed(
                    self.device_id, speed, mode
                ),
                cloud_applied=None,
                intent=intent,
            )

        await self.commands.async_route_prepared(prepare)

    async def async_set_swing(self, direction: str, value: Swing) -> None:
        """Change vertical or horizontal AC swing."""
        await self._async_cloud_command(
            lambda cloud: cloud.async_set_swing(self.device_id, direction, value),
            {f"swing:{direction}": value},
        )

    async def async_set_display_unit(self, unit: str) -> None:
        """Switch the thermostat screen between Celsius and Fahrenheit."""
        await self.commands.async_route(
            local_values={DISPLAY_UNITS: unit},
            cloud_write=lambda cloud: cloud.async_set_display_unit(
                self.device_id, unit
            ),
            cloud_applied=None,
        )

    @property
    def supports_display_unit(self) -> bool:
        """Whether either protocol supports changing screen temperature units."""
        local_units = self.local is not None and self.local.supports(DISPLAY_UNITS)
        caps = self.capabilities
        return not self.is_climate_sensor and (
            local_units or (caps is not None and {"C", "F"} <= set(caps.units))
        )

    async def async_identify(self) -> None:
        """Make the thermostat identify itself (local only)."""
        if self.local is None or not self.local_ok:
            raise translated_error("local_required", name=self.name)
        with self.history.command(self.history.command_id(), self.device_id):
            self.history.record(
                "command", operation="identify", transport="homekit", status="submitted"
            )
            try:
                await self.local.async_identify()
            except LOCAL_ERRORS as err:
                self.history.record(
                    "command",
                    operation="identify",
                    transport="homekit",
                    status="failed",
                    error=error_category(err),
                )
                raise translated_error(
                    "thermostat_unreachable", name=self.name
                ) from err
            else:
                self.history.record(
                    "command",
                    operation="identify",
                    transport="homekit",
                    status="accepted",
                    evidence="none",
                )

    async def _async_prepared_cloud_command(
        self, build: Callable[[MysaCloudCoordinator], CloudCommand]
    ) -> None:
        """Build dependent commands inside the shared thermostat write lock."""

        def prepare() -> RouteRequest[Field, Any, MysaCloudCoordinator, DeviceState]:
            cloud = self.require_cloud()
            try:
                command = build(cloud)
            except ValueError as err:
                raise HomeAssistantError(str(err)) from err
            return RouteRequest(
                local_values=None,
                cloud_write=lambda coordinator: coordinator.async_execute(
                    self.device_id, command
                ),
                cloud_applied=None,
                intent=command.expected,
            )

        await self.commands.async_route_prepared(prepare)

    async def _async_cloud_command(
        self,
        write: Callable[[MysaCloudCoordinator], Awaitable[None]],
        intent: Mapping[str, Any],
    ) -> None:
        """Order a cloud-only operation alongside commands using HomeKit."""
        await self.commands.async_route(
            local_values=None, cloud_write=write, cloud_applied=None, intent=intent
        )

    async def async_set_brightness(self, value: float, *, active: bool) -> None:
        """Adjust brightness against the latest serialized pair of settings."""

        def prepare(cloud: MysaCloudCoordinator) -> CloudCommand:
            state = self.cloud_state
            if state is None:
                raise translated_error("cloud_required", name=self.name)
            wake = (
                state.active_brightness if state.active_brightness is not None else 100
            )
            sleep = state.idle_brightness if state.idle_brightness is not None else 50
            return brightness_command(
                value,
                active=active,
                wake=cloud.command_value(self.device_id, "active_brightness", wake),
                sleep=cloud.command_value(self.device_id, "idle_brightness", sleep),
            )

        await self._async_prepared_cloud_command(prepare)

    def limit_setpoint(
        self, value: float | Setpoint, *, minimum: bool, unit: Unit = "C"
    ) -> Setpoint:
        """An owner limit, rounded and checked against the hardware range."""
        if isinstance(value, Setpoint):
            return value
        rules = self.rules
        try:
            return rules.limit(float(value), minimum=minimum, unit=unit)
        except SetpointError as err:
            raise setpoint_error(err, unit) from err

    async def async_set_setpoint_limit(
        self, value: float | Setpoint, *, minimum: bool, unit: Unit = "C"
    ) -> Setpoint:
        """Change one hardware setpoint limit."""
        setpoint = self.limit_setpoint(value, minimum=minimum, unit=unit)
        await self._async_cloud_command(
            lambda cloud: cloud.async_set_setpoint_limit(
                self.device_id, setpoint, minimum=minimum
            ),
            {"setpoint_min" if minimum else "setpoint_max": setpoint.celsius},
        )
        return setpoint

    async def async_set_temperature_offset(
        self, value: float | TemperatureOffset, *, unit: Unit = "C"
    ) -> TemperatureOffset:
        """Change the ambient sensor calibration offset (a °C or °F delta)."""
        if isinstance(value, TemperatureOffset):
            offset = value
        else:
            rules = self.rules
            try:
                offset = rules.offset(float(value), unit=unit)
            except SetpointError as err:
                raise setpoint_error(err, unit) from err
        await self._async_cloud_command(
            lambda cloud: cloud.async_set_temperature_offset(self.device_id, offset),
            {"temperature_offset": offset.celsius},
        )
        return offset

    async def async_set_button_lock(self, value: ButtonLock) -> None:
        """Change the thermostat's physical button lock."""
        await self._async_cloud_command(
            lambda cloud: cloud.async_set_button_lock(self.device_id, value),
            {"button_lock": value},
        )

    async def async_set_heater_type(self, value: HeaterType) -> None:
        """Change the relay control profile."""
        await self._async_cloud_command(
            lambda cloud: cloud.async_set_heater_type(self.device_id, value),
            {"heater_type": value},
        )

    def require_legacy_cloud(self) -> MysaCloudCoordinator:
        """Guard extras whose wire protocol has not been implemented on new models."""
        if self.basic_cloud_only:
            raise HomeAssistantError(
                "Schedules and holds are not implemented for this model"
            )
        return self.require_cloud()

    def require_cloud(self) -> MysaCloudCoordinator:
        """Return the cloud coordinator or raise a clear error."""
        cloud = self.cloud
        if cloud is None:
            raise translated_error("cloud_required", name=self.name)
        return cloud

    def raise_if_cloud_error(self, err: MysaError) -> HomeAssistantError:
        """Map a client error to a user-facing one."""
        return translated_error("cloud_write_failed", error=str(err))


# Modes Home Assistant can represent and we know how to drive.
SUPPORTED_MODES: frozenset[Mode] = frozenset(
    {Mode.OFF, Mode.HEAT, Mode.COOL, Mode.FAN_ONLY, Mode.DRY}
)
