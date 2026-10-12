"""One thermostat as Home Assistant sees it: local first, cloud as fallback.

Reads come from HomeKit while the local connection is up; the cloud fills in
only while it's down, so the two sources never fight. Writes go to HomeKit
first and fall back to the cloud. Late local writes are reconciled with newer
commands so an old request cannot remain the thermostat's final state.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from datetime import date, datetime
import math
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
    Swing,
    model_family,
)
from mysa_client.cloud.commands import (
    auto_temperature_changes,
    hvac_fan_changes,
    mode_changes,
    temperature_changes,
)
from mysa_client.cloud.const import (
    EXPERIMENTAL_CLOUD_FAMILIES,
    SWING_VERTICAL,
)
from mysa_client.homekit import Field, LocalMode

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
    CURRENT_STATE,
    CURRENT_TEMPERATURE,
    DISPLAY_UNITS,
    LOCAL_ERRORS,
    TARGET_STATE,
    TARGET_TEMPERATURE,
    LocalThermostat,
)

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

    def _mqtt_measurement(
        self, name: str, *, minimum: float, maximum: float
    ) -> float | None:
        """Return a fresh, bounded MQTT measurement."""
        mqtt = self.mqtt
        value = mqtt.value(self.device_id, name) if mqtt is not None else None
        if (
            isinstance(value, bool)
            or not isinstance(value, int | float)
            or not math.isfinite(value)
            or not minimum <= value <= maximum
        ):
            return None
        return float(value)

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

    def _local(self, ctype: Field) -> Any:
        assert self.local is not None
        return self.local.values.get(ctype)

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
        return self.family in {
            ModelFamily.BASEBOARD_V1,
            ModelFamily.BASEBOARD_V2,
            ModelFamily.BASEBOARD_LITE,
            ModelFamily.BASEBOARD_V3,
            ModelFamily.INFLOOR,
            ModelFamily.CENTRAL_HVAC,
        }

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
        if self.local_ok:
            value = self._local(CURRENT_TEMPERATURE)
            return float(value) if isinstance(value, int | float) else None
        state = self.cloud_state
        return (
            state.room_temperature
            if state
            and self.cloud
            and self.cloud.measurement_fresh(self.device_id, "room_temperature")
            else None
        )

    @property
    def humidity(self) -> float | None:
        """Room humidity in %, preferring fresh MQTT over fresh cloud data."""
        mqtt_value = self._mqtt_measurement("humidity", minimum=0, maximum=100)
        if mqtt_value is not None:
            return mqtt_value
        state = self.cloud_state
        cloud = self.cloud
        value = state.humidity if state else None
        if (
            value is None
            or not math.isfinite(value)
            or not 0 <= value <= 100
            or cloud is None
            or not cloud.measurement_fresh(self.device_id, "humidity")
        ):
            return None
        return value

    @property
    def humidity_source(self) -> str | None:
        """Source of the current humidity value, when one is fresh."""
        if self._mqtt_measurement("humidity", minimum=0, maximum=100) is not None:
            return "mqtt"
        if self.humidity is not None:
            return "cloud"
        return None

    @property
    def duty_cycle(self) -> float | None:
        """Numeric heating fraction, preferring fresh MQTT over cloud telemetry."""
        mqtt_value = self._mqtt_measurement("duty_cycle", minimum=0, maximum=1)
        if mqtt_value is not None:
            return mqtt_value
        state = self.cloud_state
        cloud = self.cloud
        value = state.duty_cycle if state else None
        if (
            value is None
            or not math.isfinite(value)
            or not 0 <= value <= 1
            or cloud is None
            or not cloud.measurement_fresh(self.device_id, "duty_cycle")
        ):
            return None
        return value

    @property
    def duty_cycle_source(self) -> str | None:
        """Source of the current numeric duty-cycle value, when one is fresh."""
        if self._mqtt_measurement("duty_cycle", minimum=0, maximum=1) is not None:
            return "mqtt"
        if self.duty_cycle is not None:
            return "cloud"
        return None

    @property
    def mode(self) -> Mode | None:
        """Current mode."""
        if self.local_ok:
            value = self._local(TARGET_STATE)
            return LOCAL_MODE_TO_MODE.get(value)
        state = self.cloud_state
        return state.mode if state else None

    @property
    def target_temperature(self) -> float | None:
        """Setpoint in °C for the current mode."""
        if self.local_ok:
            value = self._local(TARGET_TEMPERATURE)
            return float(value) if isinstance(value, int | float) else None
        state = self.cloud_state
        return state.setpoint_for(state.mode) if state else None

    @property
    def action(self) -> str | None:
        """off, idle, heating or cooling (the real relay state)."""
        mode = self.mode
        if self.is_hvac:
            state, cloud = self.cloud_state, self.cloud
            if not state or not cloud:
                return None
            if state.hvac_stages is not None and cloud.measurement_fresh(
                self.device_id, "hvac_stages"
            ):
                stages = state.hvac_stages
                heat = any(
                    stages[k] for k in ("HeatingStage1", "HeatingStage2", "AuxHeat")
                )
                cool = any(stages[k] for k in ("CoolingStage1", "CoolingStage2"))
                if heat and cool:
                    return None
                if heat:
                    return ACTION_HEATING
                if cool:
                    return ACTION_COOLING
                return ACTION_OFF if mode == Mode.OFF else ACTION_IDLE
            if state.hvac_state is not None and cloud.measurement_fresh(
                self.device_id, "hvac_state"
            ):
                heat = bool(state.hvac_state & (256 | 512 | 4096))
                cool = bool(state.hvac_state & (1024 | 2048))
                if heat and cool:
                    return None
                if heat:
                    return ACTION_HEATING
                if cool:
                    return ACTION_COOLING
                if state.hvac_state & 8192:
                    return "fan"
                return ACTION_OFF if mode == Mode.OFF else ACTION_IDLE
            return None
        if mode == Mode.OFF:
            return ACTION_OFF
        if self.local_ok:
            value = self._local(CURRENT_STATE)
            if value == ACTION_HEATING:
                return ACTION_HEATING
            if value == ACTION_COOLING:
                return ACTION_COOLING
            return ACTION_IDLE if value == ACTION_IDLE else None
        state = self.cloud_state
        if (
            state is None
            or state.heating is None
            or not self.cloud
            or not self.cloud.measurement_fresh(self.device_id, "duty_cycle")
        ):
            return None
        if not state.heating:
            return ACTION_IDLE
        return ACTION_COOLING if mode == Mode.COOL else ACTION_HEATING

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
        mqtt_duty = self._mqtt_measurement("duty_cycle", minimum=0, maximum=1)
        if mqtt_duty is not None:
            mode = self.mode
            return 0.0 if mode is not None and mode != Mode.HEAT else mqtt_duty
        if self.local_ok:
            heating = self.heating
            return float(heating) if heating is not None else None
        state = self.cloud_state
        if not self.cloud_ok or state is None or state.mode is None:
            return None
        if state.mode != Mode.HEAT:
            return 0.0
        if not self.cloud or not self.cloud.measurement_fresh(
            self.device_id, "duty_cycle"
        ):
            return None
        return self.duty_cycle

    @property
    def display_unit(self) -> str | None:
        """C or F, as shown on the thermostat's own screen."""
        if self.local_ok:
            value = self._local(DISPLAY_UNITS)
            if value in ("C", "F"):
                return str(value)
        record = self.cloud_record
        if not self.basic_cloud_only and record is not None and record.display_unit:
            return record.display_unit
        state = self.cloud_state
        return state.display_unit if state else None

    @property
    def supported_modes(self) -> list[Mode]:
        """Modes this model supports, from the capability data or HomeKit."""
        caps = self.capabilities
        if self.basic_cloud_only:
            modes = caps.basic_modes(self.family) if caps else []
            state = self.cloud_state
            if state and state.auto_enabled == 0:
                modes = [m for m in modes if m != Mode.AUTO]
            return modes
        if caps is not None and caps.modes:
            return [m for m in caps.modes if m in SUPPORTED_MODES]
        if self.local is not None and self.local.supports(TARGET_STATE):
            return [LOCAL_MODE_TO_MODE[m] for m in self.local.supported_modes]
        return [Mode.OFF, Mode.HEAT]

    @property
    def setpoint_range(self) -> tuple[float, float, float]:
        """(min, max, step) for the setpoint, honoring the hardware limits."""
        caps = self.capabilities
        if self.is_hvac and caps:
            # HA validates before applying a requested mode change. Expose the
            # envelope; the write boundary enforces each mode's own limits.
            ranges = [
                r
                for r in (
                    caps.heat_setpoint_range,
                    caps.cool_setpoint_range,
                    caps.auto_heat_range,
                    caps.auto_cool_range,
                )
                if r
            ]
            if ranges:
                return (
                    min(r[0] for r in ranges),
                    max(r[1] for r in ranges),
                    min(r[2] for r in ranges),
                )
        low = high = step = None
        if self.local is not None:
            low, high, step = self.local.temperature_range
        caps = self.capabilities
        if caps is not None and caps.heat_setpoint_range is not None:
            c_low, c_high, c_step = caps.heat_setpoint_range
            low = c_low if low is None else max(low, c_low)
            high = c_high if high is None else min(high, c_high)
            step = step or c_step
        setpoint_min = self.setpoint_min
        if setpoint_min is not None:
            low = setpoint_min if low is None else max(low, setpoint_min)
        setpoint_max = self.setpoint_max
        if setpoint_max is not None:
            high = setpoint_max if high is None else min(high, setpoint_max)
        low = 5.0 if low is None else float(low)
        high = 30.0 if high is None else float(high)
        if high <= low:
            high = low + 0.5
        return low, high, float(step or 0.5)

    @property
    def hvac_fan_modes(self) -> list[str]:
        """Native fan operation, distinct from an AC's IR fan speeds."""
        caps = self.capabilities
        return list(caps.hvac_fan_operations) if self.is_hvac and caps else []

    @property
    def hvac_fan_mode(self) -> str | None:
        """Confirmed fan operation; a configured circulation cycle is runtime."""
        state = self.cloud_state
        if state is None or state.fan_always_on is None:
            return None
        if state.fan_always_on == 1:
            return "on"
        if state.fan_runtime is None or state.fan_period is None:
            return None
        if state.fan_runtime > 0:
            return "runtime"
        return "auto" if state.fan_period > 0 else "off"

    async def async_set_basic_temperature(
        self,
        *,
        mode: Mode | None = None,
        temperature: float | None = None,
        low: float | None = None,
        high: float | None = None,
    ) -> None:
        """One validated state update for a mode and its associated targets."""
        cloud = self.require_cloud()
        selected = (
            mode
            if mode is not None
            else cloud.command_value(self.device_id, "mode", self.mode)
        )
        changes = mode_changes(mode) if mode is not None else {}
        if low is not None or high is not None:
            if (
                not self.is_hvac
                or temperature is not None
                or selected != Mode.AUTO
                or low is None
                or high is None
            ):
                raise HomeAssistantError(
                    "Supply both low and high setpoints in heat/cool mode"
                )
            changes.update(auto_temperature_changes(low, high))
        if temperature is not None:
            if selected not in (Mode.HEAT, Mode.COOL, Mode.OFF):
                raise HomeAssistantError("A single setpoint requires heat or cool mode")
            changes.update(temperature_changes(selected, temperature))
        if changes:
            await self._async_cloud_command(
                lambda c: c.async_update_state(self.device_id, changes),
                {"climate": changes},
            )

    @property
    def hvac_fan_speeds(self) -> tuple[int, ...]:
        """Speeds explicitly advertised in the current Central HVAC mode."""
        caps = self.capabilities
        return caps.hvac_fan_speeds(self.mode) if self.is_hvac and caps else ()

    async def async_set_hvac_fan_speed(self, speed: int) -> None:
        """Change native fan speed without AC enum conversion."""
        await self._async_cloud_command(
            lambda c: c.async_update_state(
                self.device_id, {"modes": {"fan_mode": speed}}
            ),
            {"hvac_fan_speed": speed},
        )

    async def async_set_hvac_fan_mode(self, mode: str) -> None:
        """Set native fan operation through the structured state endpoint."""
        if mode not in self.hvac_fan_modes:
            raise HomeAssistantError("Unsupported fan operation")
        changes = hvac_fan_changes(mode)
        await self._async_cloud_command(
            lambda c: c.async_update_state(self.device_id, changes),
            {"fan_operation": mode},
        )

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

    async def async_set_temperature(self, value: float) -> None:
        """Change the setpoint for the current mode."""
        if self.basic_cloud_only:
            await self.async_set_basic_temperature(temperature=value)
            return
        mode = (
            self.cloud.command_value(self.device_id, "mode", self.mode)
            if self.cloud
            else self.mode
        )

        async def _cloud(cloud: MysaCloudCoordinator) -> None:
            await cloud.async_set_temperature(self.device_id, mode or Mode.HEAT, value)

        await self.commands.async_route(
            local_values={TARGET_TEMPERATURE: value},
            cloud_write=_cloud,
            cloud_applied=lambda state: state.setpoint_for(mode) == value,
        )

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
        """Change the AC fan speed, keeping its current mode in the same command."""
        mode = (
            self.cloud.command_value(self.device_id, "mode", self.mode)
            if self.cloud
            else self.mode
        )
        intent: dict[str, Any] = {"fan_speed": speed}
        if mode is not None:
            intent[TARGET_STATE] = mode.name.lower()
        await self._async_cloud_command(
            lambda cloud: cloud.async_set_fan_speed(self.device_id, speed, mode), intent
        )

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
        """Adjust brightness in app-sized steps, keeping sleep no brighter than wake."""
        state = self.cloud_state
        if state is None:
            raise translated_error("cloud_required", name=self.name)
        wake = state.active_brightness if state.active_brightness is not None else 100
        sleep = state.idle_brightness if state.idle_brightness is not None else 50
        if self.cloud:
            wake = self.cloud.command_value(self.device_id, "active_brightness", wake)
            sleep = self.cloud.command_value(self.device_id, "idle_brightness", sleep)
        value = round(value / 5) * 5
        if active:
            wake = max(10, value)
            sleep = min(sleep, wake)
        else:
            sleep = value
            wake = max(wake, sleep)
        await self._async_cloud_command(
            lambda cloud: cloud.async_set_display_brightness(
                self.device_id, active=wake, idle=sleep
            ),
            {"brightness_active": wake, "brightness_idle": sleep},
        )

    async def async_set_setpoint_limit(self, value: float, *, minimum: bool) -> None:
        """Change one hardware setpoint limit."""
        await self._async_cloud_command(
            lambda cloud: cloud.async_set_setpoint_limit(
                self.device_id, value, minimum=minimum
            ),
            {"setpoint_min" if minimum else "setpoint_max": value},
        )

    async def async_set_temperature_offset(self, value: float) -> None:
        """Change the ambient sensor calibration offset."""
        await self._async_cloud_command(
            lambda cloud: cloud.async_set_temperature_offset(self.device_id, value),
            {"temperature_offset": value},
        )

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
