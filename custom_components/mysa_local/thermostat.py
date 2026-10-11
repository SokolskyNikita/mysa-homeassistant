"""One thermostat as Home Assistant sees it: local first, cloud as fallback.

Reads come from HomeKit while the local connection is up; the cloud fills in
only while it's down, so the two sources never fight. Writes go to HomeKit
first and fall back to the cloud, without ever applying a change twice.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from datetime import date, datetime
from typing import TYPE_CHECKING, Any

from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util

from .account import cloud_account_status
from .api import (
    MODEL_NAMES,
    Capabilities,
    DeviceRecord,
    DeviceState,
    FirmwareInfo,
    Mode,
    MysaError,
)
from .api.const import SETPOINT_SECTION_BY_MODE
from .cloud import MysaCloudCoordinator, translated_error
from .const import (
    CONF_CLOUD_RUNTIME,
    CONF_DEVICE_ID,
    CONF_HEATER_WATTS,
    CONF_MODEL,
    CONNECTION_CLOUD,
    CONNECTION_LOCAL,
    CONNECTION_OFFLINE,
    LOCAL_WRITE_TIMEOUT,
    LOGGER,
)
from .local import (
    CURRENT_STATE,
    CURRENT_TEMPERATURE,
    DISPLAY_UNITS,
    LOCAL_ERRORS,
    TARGET_STATE,
    TARGET_TEMPERATURE,
    LocalThermostat,
    LocalWriteRejectedError,
)

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigSubentry

# HomeKit TargetHeatingCoolingState values.
HK_MODE_TO_MODE: dict[int, Mode] = {
    0: Mode.OFF,
    1: Mode.HEAT,
    2: Mode.COOL,
    3: Mode.AUTO,
}
MODE_TO_HK_MODE: dict[Mode, int] = {v: k for k, v in HK_MODE_TO_MODE.items()}

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

    def _roll(self, now: datetime) -> None:
        if now.date() == self.day:
            return
        midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
        if self._since is not None:
            self.seconds += max(0.0, (midnight - self._since).total_seconds())
            self._since = midnight
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
    ) -> None:
        """Wire up one thermostat. `cloud_getter` returns None while cloud is off."""
        self.hass = hass
        self.subentry_id = subentry.subentry_id
        self.device_id: str = subentry.data[CONF_DEVICE_ID]
        self.name = subentry.title
        self.local = local
        self._cloud_getter = cloud_getter
        self.heater_watts: float = float(subentry.data.get(CONF_HEATER_WATTS) or 0)
        self.use_cloud_runtime: bool = subentry.data.get(CONF_CLOUD_RUNTIME, True)
        self._model: str | None = subentry.data.get(CONF_MODEL)
        self._listeners: set[CALLBACK_TYPE] = set()
        self.runtime = RuntimeTracker()
        self._in_fallback = False
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
        self._update_fallback()
        self.runtime.update(self.heating if self.available else None)
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
        """Seconds of heating today: the thermostat's own figure if enabled."""
        cloud_value = self.cloud_runtime_today
        if cloud_value is not None:
            return cloud_value
        return self.runtime.total()

    @property
    def runtime_source(self) -> str:
        """Cloud or local, whichever feeds runtime_today."""
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
        return (
            self.local is not None and self.local.available and bool(self.local.chars)
        )

    @property
    def cloud_ok(self) -> bool:
        """True while the cloud has fresh data and sees the thermostat online."""
        cloud = self.cloud
        if cloud is None or not cloud.last_update_success:
            return False
        state = cloud.state(self.device_id)
        return state is not None and state.connected is not False

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

    def _local(self, ctype: str) -> Any:
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
        return state.room_temperature if state else None

    @property
    def mode(self) -> Mode | None:
        """Current mode."""
        if self.local_ok:
            value = self._local(TARGET_STATE)
            return HK_MODE_TO_MODE.get(value) if isinstance(value, int) else None
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
        if mode == Mode.OFF:
            return ACTION_OFF
        if self.local_ok:
            value = self._local(CURRENT_STATE)
            if value == 1:
                return ACTION_HEATING
            if value == 2:
                return ACTION_COOLING
            return ACTION_IDLE if value == 0 else None
        state = self.cloud_state
        if state is None or state.heating is None:
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
    def display_unit(self) -> str | None:
        """C or F, as shown on the thermostat's own screen."""
        if self.local_ok:
            value = self._local(DISPLAY_UNITS)
            if value in (0, 1):
                return "F" if value == 1 else "C"
        record = self.cloud_record
        if record is not None and record.display_unit:
            return record.display_unit
        state = self.cloud_state
        return state.display_unit if state else None

    @property
    def supported_modes(self) -> list[Mode]:
        """Modes this model supports, from the capability data or HomeKit."""
        caps = self.capabilities
        if caps is not None and caps.modes:
            return [m for m in caps.modes if m in SUPPORTED_MODES]
        if self.local is not None and TARGET_STATE in self.local.chars:
            valid = self.local.chars[TARGET_STATE].valid_values or (0, 1)
            return [HK_MODE_TO_MODE[v] for v in valid if v in HK_MODE_TO_MODE]
        return [Mode.OFF, Mode.HEAT]

    @property
    def setpoint_range(self) -> tuple[float, float, float]:
        """(min, max, step) for the setpoint, honoring the hardware limits."""
        low = high = step = None
        if self.local is not None and (
            meta := self.local.chars.get(TARGET_TEMPERATURE)
        ):
            low, high, step = meta.min_value, meta.max_value, meta.min_step
        caps = self.capabilities
        if caps is not None and caps.heat_setpoint_range is not None:
            c_low, c_high, c_step = caps.heat_setpoint_range
            low = c_low if low is None else max(low, c_low)
            high = c_high if high is None else min(high, c_high)
            step = step or c_step
        state = self.cloud_state
        if state is not None:
            if state.setpoint_min is not None:
                low = (
                    state.setpoint_min if low is None else max(low, state.setpoint_min)
                )
            if state.setpoint_max is not None:
                high = (
                    state.setpoint_max
                    if high is None
                    else min(high, state.setpoint_max)
                )
        low = 5.0 if low is None else float(low)
        high = 30.0 if high is None else float(high)
        if high <= low:
            high = low + 0.5
        return low, high, float(step or 0.5)

    # ---- writes --------------------------------------------------------

    async def _async_route(
        self,
        *,
        local_values: Mapping[str, Any] | None,
        cloud_write: Callable[[MysaCloudCoordinator], Awaitable[None]] | None,
        cloud_applied: Callable[[DeviceState], bool] | None,
    ) -> None:
        """Write locally if possible, else through the cloud, never twice."""
        outcome_unknown = False
        local = self.local
        if (
            local_values is not None
            and local is not None
            and self.local_ok
            and all(k in local.chars for k in local_values)
        ):
            task = local.start_write(local_values)
            done, _ = await asyncio.wait({task}, timeout=LOCAL_WRITE_TIMEOUT)
            if task in done:
                error = task.exception()
                if error is None:
                    self.notify()
                    return
                if isinstance(error, LocalWriteRejectedError):
                    raise translated_error("local_write_rejected", error=str(error))
                local.mark_unavailable(str(error) or type(error).__name__)
            else:
                outcome_unknown = True
                local.mark_unavailable("write timed out")
                # Read back before retrying anywhere.
                try:
                    values = await local.async_read(local_values)
                except LOCAL_ERRORS:
                    values = {}
                if values and all(values.get(k) == v for k, v in local_values.items()):
                    local.values.update(values)
                    local.mark_available()
                    self.notify()
                    return

        cloud = self.cloud
        if cloud_write is None or cloud is None:
            raise translated_error("thermostat_unreachable", name=self.name)

        if outcome_unknown and cloud_applied is not None:
            try:
                state = await cloud.async_fetch_state(self.device_id)
            except MysaError as err:
                LOGGER.debug(
                    "%s: couldn't verify the write in the cloud: %s", self.name, err
                )
            else:
                if cloud_applied(state):
                    LOGGER.debug("%s: the timed-out local write had landed", self.name)
                    return
        await cloud_write(cloud)

    async def async_set_temperature(self, value: float) -> None:
        """Change the setpoint for the current mode."""
        mode = self.mode
        section = SETPOINT_SECTION_BY_MODE.get(mode or Mode.HEAT, "targetHeat")

        async def _cloud(cloud: MysaCloudCoordinator) -> None:
            await cloud.async_update_state(
                self.device_id, {section: {"setpoint": value}}
            )

        def _applied(state: DeviceState) -> bool:
            return state.setpoint_for(mode) == value

        await self._async_route(
            local_values={TARGET_TEMPERATURE: value},
            cloud_write=_cloud,
            cloud_applied=_applied,
        )

    async def async_set_mode(self, mode: Mode) -> None:
        """Change the mode."""
        hk_value = MODE_TO_HK_MODE.get(mode)

        async def _cloud(cloud: MysaCloudCoordinator) -> None:
            await cloud.async_update_state(
                self.device_id, {"modes": {"mode": int(mode)}}
            )

        def _applied(state: DeviceState) -> bool:
            return state.mode == mode

        await self._async_route(
            local_values={TARGET_STATE: hk_value} if hk_value is not None else None,
            cloud_write=_cloud,
            cloud_applied=_applied,
        )

    async def async_set_display_unit(self, unit: str) -> None:
        """Switch the thermostat's own screen between °C and °F."""

        async def _cloud(cloud: MysaCloudCoordinator) -> None:
            await cloud.async_update_settings(
                self.device_id, {"Format": "fahrenheit" if unit == "F" else "celsius"}
            )

        await self._async_route(
            local_values={DISPLAY_UNITS: 1 if unit == "F" else 0},
            cloud_write=_cloud,
            cloud_applied=None,
        )

    async def async_identify(self) -> None:
        """Make the thermostat identify itself (local only)."""
        if self.local is None or not self.local_ok:
            raise translated_error("local_required", name=self.name)
        try:
            await self.local.async_identify()
        except LOCAL_ERRORS as err:
            raise translated_error("thermostat_unreachable", name=self.name) from err

    async def async_cloud_update(
        self, changes: Mapping[str, Mapping[str, Any]]
    ) -> None:
        """Write a cloud-only setting (lock, brightness, limits, offset...)."""
        cloud = self.require_cloud()
        await cloud.async_update_state(self.device_id, changes)

    async def async_cloud_settings(self, fields: Mapping[str, Any]) -> None:
        """Write a cloud-only settings-record field (heater type...)."""
        cloud = self.require_cloud()
        await cloud.async_update_settings(self.device_id, fields)

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
