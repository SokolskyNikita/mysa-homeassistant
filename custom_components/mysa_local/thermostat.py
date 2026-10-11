"""One thermostat as Home Assistant sees it: local first, cloud as fallback.

Reads come from HomeKit while the local connection is up; the cloud fills in
only while it's down, so the two sources never fight. Writes go to HomeKit
first and fall back to the cloud. Late local writes are reconciled with newer
commands so an old request cannot remain the thermostat's final state.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from datetime import date, datetime
import math
from typing import TYPE_CHECKING, Any

from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util

from .account import cloud_account_status
from .api import (
    MODEL_NAMES,
    AcFan,
    Capabilities,
    DeviceRecord,
    DeviceState,
    FirmwareInfo,
    Mode,
    ModelFamily,
    MysaConnectionError,
    MysaError,
    MysaRateLimitError,
    Swing,
    model_family,
)
from .api.const import (
    AC_FAN_TO_STATE,
    SETPOINT_SECTION_BY_MODE,
    STATE_TO_AC_FAN,
    SWING_VERTICAL,
)
from .cloud import MysaCloudCoordinator, translated_error
from .const import (
    CLOUD_TELEMETRY_FUTURE_TOLERANCE,
    CLOUD_TELEMETRY_MAX_AGE,
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
        self._write_lock = asyncio.Lock()
        self._pending_local: asyncio.Task[None] | None = None
        self._pending_values: dict[str, Any] = {}
        self._reconcile: dict[
            str, Callable[[MysaCloudCoordinator], Awaitable[None]]
        ] = {}
        self._reconcile_task: asyncio.Task[None] | None = None
        self._stopped = False
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
        # Cloud dutyCycle is an interval fraction, not an instantaneous relay
        # state. During fallback only the device's measured daily usage counts.
        self.runtime.update(self.heating if self.local_ok else None)
        self._schedule_reconciliation()
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
        return (
            self.local is not None
            and self.local.available
            and bool(self.local.chars)
            and not self._reconcile
        )

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
        return -CLOUD_TELEMETRY_FUTURE_TOLERANCE <= age <= CLOUD_TELEMETRY_MAX_AGE

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
        return state.room_temperature if state else None

    @property
    def humidity(self) -> float | None:
        """Room humidity in % (cloud only; HomeKit doesn't expose it)."""
        state = self.cloud_state
        return state.humidity if state else None

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
    def heat_fraction(self) -> float | None:
        """Relay load locally, or the latest interval's average load in the cloud."""
        if self.local_ok:
            heating = self.heating
            return float(heating) if heating is not None else None
        state = self.cloud_state
        if not self.cloud_ok or state is None or state.mode is None:
            return None
        if state.mode != Mode.HEAT:
            return 0.0
        duty = state.duty_cycle
        if duty is None or not math.isfinite(duty) or not 0 <= duty <= 1:
            return None
        return duty

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
        return STATE_TO_AC_FAN.get(state.fan_mode)

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
        """Stop reconciliation when this thermostat's entry is unloaded."""
        self._stopped = True
        if self._reconcile_task is not None:
            self._reconcile_task.cancel()

    async def async_stop(self) -> None:
        """Close the HAP session before cancelling and reconciling pending writes."""
        self.stop()
        if self._reconcile_task is not None:
            await asyncio.gather(self._reconcile_task, return_exceptions=True)
        async with self._write_lock:
            if self.local is not None:
                await self.local.async_stop()
            if self._pending_local is not None and not self._pending_local.done():
                # Normal timeouts leave requests alone to preserve HAP framing.
                # On unload the session is closed first, so cancelling cannot
                # corrupt a later request on that session.
                self._pending_local.cancel()
                await asyncio.gather(self._pending_local, return_exceptions=True)
        await self._async_reconcile(allow_stopped=True)

    @callback
    def _local_write_done(self, task: asyncio.Task[None]) -> None:
        """Observe late failures and reconcile commands after the request settles."""
        if not task.cancelled():
            task.exception()
        self._schedule_reconciliation()

    @callback
    def _schedule_reconciliation(self) -> None:
        if (
            self._stopped
            or self.hass.is_stopping
            or self._pending_local is None
            or not self._pending_local.done()
            or (self._reconcile_task is not None and not self._reconcile_task.done())
        ):
            return
        self._reconcile_task = self.hass.async_create_background_task(
            self._async_reconcile(), f"mysa {self.name} reconcile local write"
        )

    async def _async_reconcile(self, *, allow_stopped: bool = False) -> None:
        """Reassert newer successful commands after an older local write finishes."""
        async with self._write_lock:
            if (
                (self._stopped and not allow_stopped)
                or self._pending_local is None
                or not self._pending_local.done()
            ):
                return
            if self._reconcile:
                cloud = self.cloud
                if cloud is None:
                    return
                assert self.local is not None
                self.local.mark_unavailable("confirming newer cloud commands")
                try:
                    writes: list[Callable[[MysaCloudCoordinator], Awaitable[None]]] = []
                    for write in self._reconcile.values():
                        if not any(write is pending for pending in writes):
                            writes.append(write)
                    for write in writes:
                        await write(cloud)
                        for key, pending in list(self._reconcile.items()):
                            if pending is write:
                                del self._reconcile[key]
                except HomeAssistantError as err:
                    # Keep the desired commands and retry on the next update.
                    LOGGER.warning(
                        "%s: couldn't reconcile a late write: %s", self.name, err
                    )
                    return
            self._pending_local = None
            self._pending_values.clear()
            self.notify()

    async def _async_route(
        self,
        *,
        local_values: Mapping[str, Any] | None,
        cloud_write: Callable[[MysaCloudCoordinator], Awaitable[None]] | None,
        cloud_applied: Callable[[DeviceState], bool] | None,
        intent: Mapping[str, Any] | None = None,
    ) -> None:
        """Serialize commands and remember those that supersede an in-flight write."""
        async with self._write_lock:
            if self._stopped:
                raise translated_error("thermostat_unreachable", name=self.name)
            intent_values = intent if intent is not None else local_values
            try:
                await self._async_route_locked(
                    local_values=local_values,
                    cloud_write=cloud_write,
                    cloud_applied=cloud_applied,
                    intent=intent_values,
                )
            except HomeAssistantError as err:
                if isinstance(err.__cause__, MysaConnectionError) and not isinstance(
                    err.__cause__, MysaRateLimitError
                ):
                    # A lost response doesn't mean the newer write wasn't applied.
                    self._remember_intent(intent_values, cloud_write)
                raise
            except asyncio.CancelledError:
                self._remember_intent(intent_values, cloud_write)
                raise
            else:
                self._remember_intent(intent_values, cloud_write)

    @callback
    def _remember_intent(
        self,
        values: Mapping[str, Any] | None,
        cloud_write: Callable[[MysaCloudCoordinator], Awaitable[None]] | None,
    ) -> None:
        """Keep later commands, including those whose cloud response was lost."""
        if self._pending_local is None or not values or cloud_write is None:
            return
        for key, value in values.items():
            self._reconcile.pop(key, None)
            if key not in self._pending_values or value != self._pending_values[key]:
                # Reassert mode commands too: hardware can change more than one
                # characteristic. Reinserting keys preserves latest-command order.
                self._reconcile[key] = cloud_write
        self.notify()

    async def _async_route_locked(
        self,
        *,
        local_values: Mapping[str, Any] | None,
        cloud_write: Callable[[MysaCloudCoordinator], Awaitable[None]] | None,
        cloud_applied: Callable[[DeviceState], bool] | None,
        intent: Mapping[str, Any] | None,
    ) -> None:
        """Try HomeKit, then read back an uncertain result before cloud fallback."""
        outcome_unknown = False
        local = self.local
        if (
            local_values is not None
            and local is not None
            and self.local_ok
            and self._pending_local is None
            and all(k in local.chars for k in local_values)
        ):
            task = local.start_write(local_values)
            self._pending_local = task
            self._pending_values = dict(intent or local_values)
            task.add_done_callback(self._local_write_done)
            done, _ = await asyncio.wait({task}, timeout=LOCAL_WRITE_TIMEOUT)
            if task in done:
                self._pending_local = None
                self._pending_values.clear()
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
            intent={TARGET_STATE: int(mode)},
        )

    async def async_set_fan_speed(self, speed: AcFan) -> None:
        """Change the AC's fan speed (cloud only).

        Like Mysa's app, send the current mode along with an AC's fan speed.
        """
        fields: dict[str, int] = {"fan_mode": AC_FAN_TO_STATE[speed]}
        if (mode := self.mode) is not None:
            fields["mode"] = int(mode)
        await self.async_cloud_update({"modes": fields})

    async def async_set_swing(self, direction: str, value: Swing) -> None:
        """Change the AC's vertical or horizontal swing (cloud only)."""
        await self.async_cloud_update({"modes": {f"{direction}State": int(value)}})

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

        async def _write(cloud: MysaCloudCoordinator) -> None:
            await cloud.async_update_state(self.device_id, changes)

        await self._async_route(
            local_values=None,
            cloud_write=_write,
            cloud_applied=None,
            intent={
                (
                    TARGET_STATE
                    if section == "modes" and key == "mode"
                    else f"cloud:{section}.{key}"
                ): value
                for section, fields in changes.items()
                for key, value in fields.items()
            },
        )

    async def async_cloud_settings(self, fields: Mapping[str, Any]) -> None:
        """Write a cloud-only settings-record field (heater type...)."""

        async def _write(cloud: MysaCloudCoordinator) -> None:
            await cloud.async_update_settings(self.device_id, fields)

        await self._async_route(
            local_values=None,
            cloud_write=_write,
            cloud_applied=None,
            intent={f"settings:{key}": value for key, value in fields.items()},
        )

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
