"""Polling Mysa's cloud for every thermostat on the account."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterable, Mapping
import contextlib
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
import time
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util
from mysa_client.cloud import (
    AcFan,
    ButtonLock,
    Capabilities,
    DeviceRecord,
    DeviceState,
    FirmwareInfo,
    HeaterType,
    Mode,
    MysaAuthError,
    MysaClient,
    MysaConnectionError,
    MysaError,
    MysaNotFoundError,
    MysaRateLimitError,
    Swing,
)
from mysa_client.cloud.commands import (
    button_lock_changes,
    display_brightness_changes,
    display_unit_settings,
    fan_speed_changes,
    heater_type_settings,
    mode_changes,
    setpoint_limit_changes,
    swing_changes,
    temperature_changes,
    temperature_offset_changes,
)
from mysa_client.cloud.const import (
    EXPERIMENTAL_CLOUD_FAMILIES,
    ModelFamily,
    model_family,
)
from mysa_client.cloud.observations import MEASUREMENT_MAX_AGE, STATE_FIELDS, number

from .cloud_state import PendingCommand, pending_command, reconcile
from .const import (
    CLOUD_CAPABILITIES_INTERVAL,
    CLOUD_FALLBACK_INTERVAL,
    CLOUD_FIRMWARE_INTERVAL,
    CLOUD_HOLD_INTERVAL,
    CLOUD_LIVE_MODE_RENEW,
    CLOUD_LIVE_MODE_TIMEOUT,
    CLOUD_MAX_BACKOFF,
    CLOUD_RECORDS_INTERVAL,
    CLOUD_REFRESH_AFTER_WRITE,
    CLOUD_USAGE_INTERVAL,
    CLOUD_WRITE_SPACING,
    DOMAIN,
    LOGGER,
)
from .event_history import EventHistory, error_category

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry


@dataclass(slots=True)
class CloudData:
    """Everything the cloud knows about the thermostats we care about."""

    states: dict[str, DeviceState] = field(default_factory=dict)
    records: dict[str, DeviceRecord] = field(default_factory=dict)
    capabilities: dict[str, Capabilities] = field(default_factory=dict)
    firmware: dict[str, FirmwareInfo] = field(default_factory=dict)
    usage_today: dict[str, tuple[datetime, float]] = field(default_factory=dict)
    holds: dict[str, dict[str, Any] | None] = field(default_factory=dict)


def _local_midnight(now: datetime, time_zone: str | None) -> datetime:
    tz = dt_util.get_default_time_zone()
    if time_zone:
        with contextlib.suppress(ZoneInfoNotFoundError):
            tz = ZoneInfo(time_zone)
    local = now.astimezone(tz)
    return local.replace(hour=0, minute=0, second=0, microsecond=0)


def translated_error(key: str, **placeholders: str) -> HomeAssistantError:
    """A HomeAssistantError with a translated message."""
    return HomeAssistantError(
        translation_domain=DOMAIN,
        translation_key=key,
        translation_placeholders=placeholders or None,
    )


class MysaCloudCoordinator(DataUpdateCoordinator[CloudData]):
    """Polls /state/batch for all thermostats, plus slower-moving extras."""

    config_entry: ConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: MysaClient,
        *,
        device_ids: Iterable[str],
        scan_interval: int,
        usage_device_ids: Iterable[str] = (),
        device_models: Mapping[str, str | None] | None = None,
        live_mode: bool = False,
        history: EventHistory | None = None,
    ) -> None:
        """Create the coordinator; nothing is fetched until the first refresh."""
        self._base_interval = timedelta(seconds=scan_interval)
        super().__init__(
            hass,
            LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} cloud",
            update_interval=self._base_interval,
        )
        self.history = history if history is not None else EventHistory()
        self.client = client
        self.client.event_callback = self.history.request_event
        self.device_ids: set[str] = set(device_ids)
        self.device_models = dict(device_models or {})
        self.usage_device_ids: set[str] = set(usage_device_ids)
        self.live_mode = live_mode
        self._refresh_lock = asyncio.Lock()
        self._write_lock = asyncio.Lock()
        self._last_write = 0.0
        self._due: dict[str, float] = {}
        self.source_health: dict[str, dict[str, Any]] = {}
        self.pending: dict[str, dict[str, PendingCommand]] = {}
        self._optional_failures: dict[str, int] = {}
        self.live_devices: set[str] = set()
        self.live_sessions: dict[str, tuple[datetime, datetime | None, datetime]] = {}
        self._refresh_after_write: CALLBACK_TYPE | None = None
        self._confirmation_generation = 0
        # Thermostats whose LAN path is down while the cloud stands in.
        self.fallback: set[str] = set()

    # ---- polling -------------------------------------------------------

    def _is_due(self, what: str) -> bool:
        return time.monotonic() >= self._due.get(what, 0.0)

    def _mark_done(self, what: str, interval: timedelta) -> None:
        self._due[what] = time.monotonic() + interval.total_seconds()

    @callback
    def mark_stale(self, what: str) -> None:
        """Make the next refresh re-read `what` (records, holds, firmware...)."""
        self._due.pop(what, None)

    def _normal_interval(self) -> timedelta:
        if self.fallback and self.live_mode:
            return CLOUD_FALLBACK_INTERVAL
        return self._base_interval

    def _backoff(self) -> None:
        current = self.update_interval or self._normal_interval()
        self.update_interval = min(current * 2, CLOUD_MAX_BACKOFF)
        self.history.record(
            "backoff",
            noisy=True,
            source="cloud",
            delay_seconds=self.update_interval.total_seconds(),
        )

    @callback
    def set_fallback(self, device_id: str, active: bool) -> None:
        """Track whether a thermostat currently relies on the cloud.

        With the live mode option on, its home gets live mode and the poll
        speeds up while any does; both stop as soon as every LAN path is back.
        """
        if active == (device_id in self.fallback) or device_id not in self.device_ids:
            return
        if active:
            self.fallback.add(device_id)
        else:
            self.fallback.discard(device_id)
            homes = self._fallback_homes(self.data.records if self.data else {})
            for key in [k for k in self._due if k.startswith("live:")]:
                if key.removeprefix("live:") not in homes:
                    self._due.pop(key)
        self.history.record(
            "fallback",
            noisy=True,
            device_id=device_id,
            transport="cloud",
            active=active,
            delay_seconds=self._normal_interval().total_seconds(),
        )
        self.update_interval = self._normal_interval()
        if active:
            with self.history.detached():
                self.hass.async_create_task(self.async_request_refresh())

    def _fallback_homes(self, records: Mapping[str, DeviceRecord]) -> set[str]:
        homes = set()
        for dev in self.fallback:
            record = records.get(dev)
            if (
                record is not None
                and record.home_id
                and model_family(record.model) not in EXPERIMENTAL_CLOUD_FAMILIES
            ):
                homes.add(record.home_id)
        return homes

    async def _async_update_data(self) -> CloudData:
        async with self._refresh_lock:
            try:
                data = await self._async_fetch()
            except MysaAuthError as err:
                raise ConfigEntryAuthFailed(
                    translation_domain=DOMAIN, translation_key="auth_expired"
                ) from err
            except MysaRateLimitError as err:
                self._backoff()
                raise UpdateFailed(
                    translation_domain=DOMAIN,
                    translation_key="cloud_rate_limited",
                    retry_after=err.retry_after,
                ) from err
            except MysaError as err:
                self._backoff()
                raise UpdateFailed(
                    translation_domain=DOMAIN,
                    translation_key="cloud_unavailable",
                    translation_placeholders={"error": error_category(err)},
                ) from err
            self.update_interval = self._normal_interval()
            return data

    async def _async_fetch(self) -> CloudData:
        old = self.data or CloudData()
        data = replace(
            old,
            states=dict(old.states),
            records=dict(old.records),
            capabilities=dict(old.capabilities),
            firmware=dict(old.firmware),
            usage_today=dict(old.usage_today),
            holds=dict(old.holds),
        )
        ids = sorted(self.device_ids)

        if self._is_due("records"):
            try:
                records = await self.client.get_devices()
                data.records = records
                self._mark_done("records", CLOUD_RECORDS_INTERVAL)
                if self._optional_failures.pop("records", None):
                    self.history.record(
                        "source", noisy=True, source="records", status="recovery"
                    )
            except MysaAuthError:
                raise
            except MysaError as err:
                self._optional_backoff("records", err)

        for home in self._fallback_homes(data.records) if self.live_mode else ():
            if self._is_due(f"live:{home}"):
                await self._best_effort(self._start_live_mode, data, home)

        legacy_ids = [
            dev
            for dev in ids
            if model_family(
                data.records[dev].model
                if dev in data.records
                else self.device_models.get(dev)
            )
            not in EXPERIMENTAL_CLOUD_FAMILIES
        ]
        requests = {"S": self.client.get_states(ids)}
        if legacy_ids:
            requests["legacy"] = self.client.get_legacy_states(legacy_ids)
        results = await asyncio.gather(*requests.values(), return_exceptions=True)
        now = dt_util.utcnow()
        successful = []
        failures = []
        for source, result in zip(requests, results, strict=True):
            previous_health = self.source_health.get(source)
            if isinstance(result, MysaAuthError):
                raise result
            if isinstance(result, BaseException):
                if not isinstance(result, MysaError):
                    raise result
                self.source_health[source] = {
                    "success": False,
                    "error": type(result).__name__,
                    "fetched_at": now.isoformat(),
                }
                if previous_health is None or previous_health.get("success"):
                    self.history.record(
                        "source",
                        noisy=True,
                        source=source,
                        status="failure",
                        error=error_category(result),
                    )
                failures.append(result)
            else:
                self.source_health[source] = {
                    "success": True,
                    "fetched_at": now.isoformat(),
                    "partial_errors": dict(self.client.source_errors.get(source, {})),
                    "route": self.client.legacy_source if source == "legacy" else "S",
                }
                partial = self.client.source_errors.get(source, {})
                if (
                    previous_health
                    and (
                        not previous_health.get("success")
                        or previous_health.get("partial_errors")
                    )
                    and not partial
                ):
                    self.history.record(
                        "source", noisy=True, source=source, status="recovery"
                    )
                for dev in partial:
                    self.history.record(
                        "source",
                        noisy=True,
                        device_id=dev,
                        source=source,
                        status="failure",
                        reason="unusable",
                    )
                successful.append(result)
        if not successful:
            self._reconcile_pending(data, now)
            raise failures[0]
        for dev in ids:
            self._record_observation_issues(
                dev, [r[dev] for r in successful if dev in r], now
            )
            data.states[dev] = reconcile(
                dev,
                [r[dev] for r in successful if dev in r],
                data.records.get(dev),
                old.states.get(dev),
                now,
            )
        for dev, (requested, baseline, expires) in self.live_sessions.items():
            state = data.states.get(dev)
            if (
                now <= expires
                and state
                and state.reading_time
                and state.reading_time >= requested
                and (baseline is None or state.reading_time > baseline)
            ):
                self.live_devices.add(dev)
            elif now > expires:
                self.live_devices.discard(dev)
        self._reconcile_pending(data, now)

        # The slower data is best effort: a failure keeps the previous values.
        for dev in ids:
            if dev not in data.records and dev not in data.states:
                continue
            if self._is_due(f"caps:{dev}"):
                await self._best_effort(self._fetch_capabilities, data, dev)
            if dev not in legacy_ids:
                continue
            if self._is_due(f"firmware:{dev}"):
                await self._best_effort(self._fetch_firmware, data, dev)
            if self._is_due(f"hold:{dev}"):
                await self._best_effort(self._fetch_hold, data, dev)
            if dev in self.usage_device_ids and self._is_due(f"usage:{dev}"):
                await self._best_effort(self._fetch_usage, data, dev)
        return data

    async def _best_effort(self, fetch: Any, data: CloudData, dev: str) -> None:
        prefix = {
            "_fetch_capabilities": "caps",
            "_fetch_firmware": "firmware",
            "_fetch_hold": "hold",
            "_fetch_usage": "usage",
            "_start_live_mode": "live",
        }[fetch.__name__]
        key = f"{prefix}:{dev}"
        try:
            await fetch(data, dev)
        except MysaAuthError:
            raise
        except MysaError as err:
            self._optional_backoff(key, err)

        else:
            if self._optional_failures.pop(key, None):
                self.history.record(
                    "source",
                    noisy=True,
                    device_id=dev if prefix != "live" else None,
                    source=prefix,
                    status="recovery",
                )

    def _optional_backoff(self, key: str, err: MysaError) -> None:
        failures = self._optional_failures.get(key, 0) + 1
        self._optional_failures[key] = failures
        seconds = min(3600, 30 * 2 ** min(failures, 7))
        if isinstance(err, MysaRateLimitError) and err.retry_after:
            seconds = max(seconds, err.retry_after)
        if isinstance(err, MysaNotFoundError):
            seconds = 86400
        self._mark_done(key, timedelta(seconds=seconds))
        source, _, dev = key.partition(":")
        self.history.record(
            "backoff",
            noisy=True,
            device_id=dev if source != "live" else None,
            source=source,
            error=error_category(err),
            delay_seconds=seconds,
        )

    async def _start_live_mode(self, data: CloudData, home: str) -> None:
        streamed = await self.client.start_live_mode(home, CLOUD_LIVE_MODE_TIMEOUT)
        self.history.record("source", noisy=True, source="live", status="accepted")
        now = dt_util.utcnow()
        for dev in streamed:
            state = data.states.get(dev)
            baseline = state.reading_time if state else None
            self.live_sessions[dev] = (
                now,
                baseline,
                now + timedelta(seconds=CLOUD_LIVE_MODE_TIMEOUT),
            )
        expected = {
            dev
            for dev in self.fallback
            if dev in data.records and data.records[dev].home_id == home
        }
        if not expected.issubset(streamed):
            raise MysaConnectionError(
                "Live reporting was not accepted for every requested device"
            )
        self._mark_done(f"live:{home}", CLOUD_LIVE_MODE_RENEW)

    async def _fetch_capabilities(self, data: CloudData, dev: str) -> None:
        data.capabilities[dev] = await self.client.get_capabilities(dev)
        self._mark_done(f"caps:{dev}", CLOUD_CAPABILITIES_INTERVAL)

    async def _fetch_firmware(self, data: CloudData, dev: str) -> None:
        data.firmware[dev] = await self.client.get_firmware_info(dev)
        self._mark_done(f"firmware:{dev}", CLOUD_FIRMWARE_INTERVAL)

    async def _fetch_hold(self, data: CloudData, dev: str) -> None:
        data.holds[dev] = await self.client.get_hold(dev)
        self._mark_done(f"hold:{dev}", CLOUD_HOLD_INTERVAL)

    async def _fetch_usage(self, data: CloudData, dev: str) -> None:
        record = data.records.get(dev)
        now = dt_util.utcnow()
        midnight = _local_midnight(now, record.time_zone if record else None)
        tz_name = str(midnight.tzinfo) if midnight.tzinfo else "UTC"
        if record and record.time_zone:
            tz_name = record.time_zone
        days = await self.client.get_usage(dev, tz_name, midnight, now)
        seconds = 0.0
        for day in days:
            if day.start >= midnight - timedelta(minutes=1) and day.heat_seconds:
                seconds += day.heat_seconds
        data.usage_today[dev] = (midnight, seconds)
        self._mark_done(f"usage:{dev}", CLOUD_USAGE_INTERVAL)

    # ---- reads and writes used by entities ------------------------------

    def device_family(self, device_id: str) -> ModelFamily | None:
        """Use saved model identity when the account list is unavailable."""
        record, state = self.record(device_id), self.state(device_id)
        return model_family(
            (record.model if record else None)
            or self.device_models.get(device_id)
            or (state.model if state else None)
        )

    def state(self, device_id: str) -> DeviceState | None:
        """Latest state of one thermostat, if the cloud has it."""
        return self.data.states.get(device_id) if self.data else None

    def command_value(self, device_id: str, name: str, fallback: Any) -> Any:
        """Use current intent for dependent writes, never for entity readings."""
        command = self.pending.get(device_id, {}).get(name)
        if (
            command
            and command.status
            in ("pending", "ambiguous", "stored", "confirmed", "unchanged")
            and dt_util.utcnow() < command.expires_at
        ):
            return command.expected.get(name, fallback)
        return fallback

    def measurement_max_age(self, device_id: str) -> timedelta:
        """Tighten freshness only after telemetry proves live reporting started."""
        session = self.live_sessions.get(device_id)
        if (
            device_id in self.live_devices
            and session
            and dt_util.utcnow() <= session[2]
        ):
            return max(
                timedelta(seconds=90), (self.update_interval or self._base_interval) * 3
            )
        return MEASUREMENT_MAX_AGE

    def measurement_fresh(self, device_id: str, name: str) -> bool:
        """Check this field's own clock, independent of the room-temperature clock."""
        state = self.state(device_id)
        obs = state.observations.get(name) if state else None
        return bool(
            obs and obs.fresh(dt_util.utcnow(), self.measurement_max_age(device_id))
        )

    def record(self, device_id: str) -> DeviceRecord | None:
        """Settings record of one thermostat."""
        return self.data.records.get(device_id) if self.data else None

    def capabilities(self, device_id: str) -> Capabilities | None:
        """Capability document of one thermostat."""
        return self.data.capabilities.get(device_id) if self.data else None

    async def async_fetch_state(self, device_id: str) -> DeviceState:
        """Read one thermostat's state right now (used to verify writes)."""
        state = await self.client.get_state(device_id)
        if self.data is not None:
            self._record_observation_issues(device_id, [state], dt_util.utcnow())
            state = reconcile(
                device_id,
                [state],
                self.data.records.get(device_id),
                self.data.states.get(device_id),
                dt_util.utcnow(),
            )
            self.data.states[device_id] = state
            self._reconcile_pending(self.data, dt_util.utcnow())
            self.async_update_listeners()
        return state

    async def _async_spaced(self, call: Callable[[], Awaitable[Any]]) -> None:
        async with self._write_lock:
            wait = self._last_write + CLOUD_WRITE_SPACING - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            try:
                await call()
            except MysaAuthError as err:
                self.config_entry.async_start_reauth(self.hass)
                raise translated_error("auth_expired") from err
            except MysaNotFoundError as err:
                raise translated_error(
                    "cloud_write_failed", error=error_category(err)
                ) from err
            except MysaConnectionError as err:
                raise translated_error(
                    "cloud_unavailable", error=error_category(err)
                ) from err
            except MysaError as err:
                raise translated_error(
                    "cloud_write_failed", error=error_category(err)
                ) from err
            finally:
                self._last_write = time.monotonic()
        self._schedule_refresh_after_write()

    @callback
    def _schedule_refresh_after_write(
        self, attempt: int = 0, generation: int | None = None
    ) -> None:
        """Bounded readbacks accommodate reports arriving after the first read."""
        if generation is None:
            self._confirmation_generation += 1
            generation = self._confirmation_generation
        if self._refresh_after_write is not None:
            self._refresh_after_write()

        async def _readback() -> None:
            await self.async_refresh()
            if (
                generation == self._confirmation_generation
                and self.last_update_success
                and attempt < 2
                and any(
                    command.status in ("pending", "ambiguous", "stored")
                    and dt_util.utcnow() < command.expires_at
                    for commands in self.pending.values()
                    for command in commands.values()
                )
            ):
                self._schedule_refresh_after_write(attempt + 1, generation)

        @callback
        def _refresh(_now: datetime) -> None:
            # Not the debounced request_refresh: its cooldown would delay the
            # confirmation of back-to-back writes by up to 10 s.
            self._refresh_after_write = None
            self.config_entry.async_create_background_task(
                self.hass, _readback(), f"{DOMAIN} refresh after write"
            )

        for dev, commands in self.pending.items():
            seen: set[str] = set()
            for command in commands.values():
                if command.correlation_id in seen or command.status not in {
                    "pending",
                    "ambiguous",
                    "stored",
                }:
                    continue
                seen.add(command.correlation_id)
                self.history.record(
                    "retry",
                    device_id=dev,
                    command_id=command.correlation_id,
                    operation="readback",
                    attempt=attempt + 1,
                    delay_seconds=CLOUD_REFRESH_AFTER_WRITE
                    if attempt == 0
                    else 5 * attempt,
                )
        with self.history.detached():
            self._refresh_after_write = async_call_later(
                self.hass,
                CLOUD_REFRESH_AFTER_WRITE if attempt == 0 else 5 * attempt,
                _refresh,
            )

    async def async_update_state(
        self, device_id: str, changes: Mapping[str, Mapping[str, Any]]
    ) -> None:
        """Write state sections through the cloud."""
        await self._async_write_state(
            device_id,
            lambda: self.client.update_state(device_id, changes),
            changes,
        )

    async def _async_write_state(
        self,
        device_id: str,
        call: Callable[[], Awaitable[Any]],
        changes: Mapping[str, Mapping[str, Any]],
    ) -> None:
        """Track intent without manufacturing device-reported state."""
        expected = {
            name: changes[section][wire]
            for name, (section, wire) in STATE_FIELDS.items()
            if section in changes and wire in changes[section]
        }
        await self._tracked_write(device_id, call, expected)

    def _record_observation_issues(
        self, dev: str, states: list[DeviceState], now: datetime
    ) -> None:
        """Explain rejected clocks and stale/mismatching command readbacks."""
        for state in states:
            for name, obs in state.observations.items():
                command = self.pending.get(dev, {}).get(name)
                reason = None
                if not obs.usable(now):
                    reason = "invalid_clock"
                elif command and command.status in {"pending", "ambiguous", "stored"}:
                    if obs.observed_at is None:
                        reason = "missing_timestamp"
                    elif obs.observed_at < command.submitted_at.replace(microsecond=0):
                        reason = "before_submission"
                    elif obs.value != command.expected.get(name):
                        reason = "value_mismatch"
                if reason:
                    self.history.record(
                        "observation",
                        noisy=True,
                        device_id=dev,
                        command_id=command.correlation_id if command else None,
                        field=name,
                        source=obs.source,
                        reason=reason,
                        observations={name: obs},
                    )

    def _command_event(
        self, dev: str, command: PendingCommand, status: str, **fields: Any
    ) -> None:
        self.history.record(
            "command",
            device_id=dev,
            command_id=command.correlation_id,
            operation="set_settings" if command.stored_fields else "set_state",
            status=status,
            transport="cloud",
            **fields,
        )

    def _reconcile_pending(self, data: CloudData, now: datetime) -> None:
        for dev, commands in self.pending.items():
            seen = set()
            for command in commands.values():
                if id(command) in seen:
                    continue
                seen.add(id(command))
                before = (command.status, dict(command.confirmation))
                command.reconcile(data.states.get(dev), data.records.get(dev), now)
                if before != (command.status, command.confirmation):
                    self._command_event(
                        dev,
                        command,
                        command.status,
                        observations=command.evidence,
                        evidence="device_report"
                        if command.status == "confirmed"
                        else "configuration_storage"
                        if command.status == "stored"
                        else "none",
                    )

    def _validate_settings(self, device_id: str, expected: dict[str, Any]) -> None:
        """Validate semantic settings against the discovered device capabilities."""
        state = self.state(device_id)
        family = self.device_family(device_id)
        caps = self.capabilities(device_id)
        if family in EXPERIMENTAL_CLOUD_FAMILIES:
            from mysa_client.cloud.validation import validate_basic_command

            try:
                validate_basic_command(
                    family,
                    caps,
                    state,
                    expected,
                    lambda name, fallback: self.command_value(
                        device_id, name, fallback
                    ),
                )
            except ValueError as err:
                raise HomeAssistantError(str(err)) from err
        if "heat_setpoint" in expected and state:
            target = number(expected["heat_setpoint"])
            low = state.reported_observations.get("setpoint_min")
            high = state.reported_observations.get("setpoint_max")
            minima = [
                v
                for v in (state.setpoint_min, low.value if low else None)
                if v is not None
            ]
            maxima = [
                v
                for v in (state.setpoint_max, high.value if high else None)
                if v is not None
            ]
            if (
                target is None
                or (minima and target < max(minima))
                or (maxima and target > min(maxima))
            ):
                raise HomeAssistantError("Target exceeds known device limits")
        caps = self.capabilities(device_id)
        if caps is None:
            return
        choices = {
            "button_lock": caps.lockout_values,
            "control_type": caps.control_types,
            "display_unit": caps.units,
        }
        for name, values in choices.items():
            if name in expected and expected[name] not in values:
                raise HomeAssistantError(f"Unsupported Mysa setting: {name}")
        ranges = {
            "temperature_offset": caps.offset_range,
            "setpoint_min": caps.heat_setpoint_range,
            "setpoint_max": caps.heat_setpoint_range,
        }
        for name, limits in ranges.items():
            if name in expected and (
                limits is None
                or number(expected[name]) is None
                or not limits[0] <= expected[name] <= limits[1]
            ):
                raise HomeAssistantError(
                    f"Mysa setting outside supported range: {name}"
                )
        if (
            "active_brightness" in expected or "idle_brightness" in expected
        ) and not caps.brightness:
            raise HomeAssistantError("Brightness is unsupported by this device")
        state = self.state(device_id)
        if state and ("setpoint_min" in expected or "setpoint_max" in expected):
            low = expected.get(
                "setpoint_min",
                self.command_value(device_id, "setpoint_min", state.setpoint_min),
            )
            high = expected.get(
                "setpoint_max",
                self.command_value(device_id, "setpoint_max", state.setpoint_max),
            )
            if low is not None and high is not None and low > high:
                raise HomeAssistantError("Minimum setpoint exceeds maximum")

    async def _tracked_write(
        self,
        device_id: str,
        call: Callable[[], Awaitable[Any]],
        expected: dict[str, Any],
        stored: dict[str, Any] | None = None,
    ) -> None:
        command = pending_command(
            expected, self.state(device_id), dt_util.utcnow(), stored
        )
        command.correlation_id = self.history.command_id()
        self._command_event(
            device_id,
            command,
            "submitted",
            requested=expected,
            baseline={k: v for k, v in command.baseline.items() if k in expected},
        )
        try:
            self._validate_settings(device_id, expected)
        except HomeAssistantError:
            self._command_event(device_id, command, "failed", error="validation_failed")
            raise
        commands = self.pending.setdefault(device_id, {})
        keys = list(expected) or [f"record:{key}" for key in (stored or {})]
        for key in keys:
            # Supersede every field of an overlapping earlier command together.
            prior = commands.get(key)
            if prior and prior.status in ("pending", "ambiguous", "stored"):
                prior.status = "superseded"
                if prior.correlation_id != command.correlation_id:
                    self._command_event(
                        device_id,
                        prior,
                        "superseded",
                        superseded_by=command.correlation_id,
                    )
            commands[key] = command
        try:
            with self.history.command(command.correlation_id, device_id):
                await self._async_spaced(call)
            self._command_event(device_id, command, "accepted", evidence="none")
            if command.status == "unchanged":
                self._command_event(device_id, command, "unchanged", evidence="none")
        except asyncio.CancelledError:
            if command.status != "superseded":
                command.status = "ambiguous"
            self._command_event(device_id, command, "ambiguous", error="cancelled")
            raise
        except HomeAssistantError as err:
            if command.status != "superseded":
                command.status = (
                    "ambiguous"
                    if isinstance(err.__cause__, MysaConnectionError)
                    and not isinstance(err.__cause__, MysaRateLimitError)
                    else "failed"
                )
            self._command_event(
                device_id,
                command,
                command.status,
                error=error_category(err.__cause__ or err),
            )
            self._schedule_refresh_after_write()
            raise
        finally:
            if stored:
                self.mark_stale("records")
            self.async_update_listeners()

    async def async_set_temperature(
        self, device_id: str, mode: Mode | None, temperature: float
    ) -> None:
        """Set a temperature target using the cloud protocol's mode mapping."""
        await self._async_write_state(
            device_id,
            lambda: self.client.set_temperature(device_id, mode, temperature),
            temperature_changes(mode, temperature),
        )

    async def async_set_mode(self, device_id: str, mode: Mode) -> None:
        """Set operating mode through the cloud."""
        await self._async_write_state(
            device_id,
            lambda: self.client.set_mode(device_id, mode),
            mode_changes(mode),
        )

    async def async_set_fan_speed(
        self, device_id: str, speed: AcFan, mode: Mode | None = None
    ) -> None:
        """Set an AC fan speed through the cloud."""
        await self._async_write_state(
            device_id,
            lambda: self.client.set_fan_speed(device_id, speed, mode),
            fan_speed_changes(speed, mode),
        )

    async def async_set_swing(
        self, device_id: str, direction: str, value: Swing
    ) -> None:
        """Set an AC swing direction through the cloud."""
        await self._async_write_state(
            device_id,
            lambda: self.client.set_swing(device_id, direction, value),
            swing_changes(direction, value),
        )

    async def async_set_display_brightness(
        self, device_id: str, *, active: int, idle: int
    ) -> None:
        """Set both display brightness values through the cloud."""
        changes = display_brightness_changes(active, idle)
        await self._async_write_state(
            device_id,
            lambda: self.client.set_display_brightness(
                device_id, active=active, idle=idle
            ),
            changes,
        )

    async def async_set_button_lock(self, device_id: str, value: ButtonLock) -> None:
        """Lock or unlock the thermostat's physical buttons through the cloud."""
        changes = button_lock_changes(value)
        await self._async_write_state(
            device_id,
            lambda: self.client.set_button_lock(device_id, value),
            changes,
        )

    async def async_set_setpoint_limit(
        self, device_id: str, value: float, *, minimum: bool
    ) -> None:
        """Set a heating setpoint bound through the cloud."""
        changes = setpoint_limit_changes(value, minimum=minimum)
        await self._async_write_state(
            device_id,
            lambda: self.client.set_setpoint_limit(device_id, value, minimum=minimum),
            changes,
        )

    async def async_set_temperature_offset(self, device_id: str, value: float) -> None:
        """Set the thermostat's room-temperature correction."""
        changes = temperature_offset_changes(value)
        await self._async_write_state(
            device_id,
            lambda: self.client.set_temperature_offset(device_id, value),
            changes,
        )

    async def async_update_settings(
        self, device_id: str, fields: Mapping[str, Any]
    ) -> None:
        """Write settings-record fields through the cloud."""
        await self._async_write_settings(
            device_id,
            lambda: self.client.update_settings(device_id, fields),
            fields,
        )

    async def _async_write_settings(
        self,
        device_id: str,
        call: Callable[[], Awaitable[Any]],
        fields: Mapping[str, Any],
    ) -> None:
        """A settings response confirms acceptance; a later read confirms storage."""
        mapping = {
            "Format": "display_unit",
            "DutyCycleOpt": "control_type",
            "MinSetpoint": "setpoint_min",
            "MaxSetpoint": "setpoint_max",
            "MaxBrightness": "active_brightness",
            "MinBrightness": "idle_brightness",
            "ButtonState": "button_lock",
        }
        if self.device_family(device_id) in EXPERIMENTAL_CLOUD_FAMILIES:
            raise HomeAssistantError(
                "Legacy device settings are not implemented for this model"
            )
        expected = {
            mapping[key]: value for key, value in fields.items() if key in mapping
        }
        if "Format" in fields:
            expected["display_unit"] = {"celsius": "C", "fahrenheit": "F"}.get(
                fields["Format"]
            )
        if "ButtonState" in fields:
            expected["button_lock"] = {"Locked": 3, "Unlocked": 0}.get(
                fields["ButtonState"]
            )
        await self._tracked_write(device_id, call, expected, dict(fields))

    async def async_set_display_unit(self, device_id: str, unit: str) -> None:
        """Set the thermostat's display unit through the cloud."""
        fields = display_unit_settings(unit)
        if self.device_family(device_id) in EXPERIMENTAL_CLOUD_FAMILIES:
            await self.async_update_state(
                device_id, {"physicalInterface": {"format": unit.upper()}}
            )
            return
        await self._async_write_settings(
            device_id,
            lambda: self.client.set_display_unit(device_id, unit),
            fields,
        )

    async def async_set_heater_type(
        self, device_id: str, heater_type: int | HeaterType
    ) -> None:
        """Set the heater profile through the cloud."""
        fields = heater_type_settings(heater_type)
        await self._async_write_settings(
            device_id,
            lambda: self.client.set_heater_type(device_id, heater_type),
            fields,
        )

    async def async_call(
        self,
        call: Callable[[], Awaitable[Any]],
        *,
        stale: Iterable[str] = (),
        device_id: str | None = None,
        operation: str = "other_write",
        requested: Mapping[str, Any] | None = None,
    ) -> None:
        """Run any other cloud write (holds, schedules, firmware) the same way."""
        with self.history.command(self.history.command_id(), device_id):
            self.history.record(
                "command",
                operation=operation,
                transport="cloud",
                status="submitted",
                requested=requested or {},
            )
            try:
                await self._async_spaced(call)
            except asyncio.CancelledError:
                self.history.record(
                    "command",
                    operation=operation,
                    transport="cloud",
                    status="ambiguous",
                    error="cancelled",
                )
                raise
            except HomeAssistantError as err:
                cause = err.__cause__ or err
                self.history.record(
                    "command",
                    operation=operation,
                    transport="cloud",
                    status="ambiguous"
                    if isinstance(cause, MysaConnectionError)
                    and not isinstance(cause, MysaRateLimitError)
                    else "failed",
                    error=error_category(cause),
                )
                raise
            else:
                self.history.record(
                    "command",
                    operation=operation,
                    transport="cloud",
                    status="accepted",
                    evidence="none",
                )
        for what in stale:
            self.mark_stale(what)

    async def async_shutdown(self) -> None:
        """Cancel the pending after-write refresh."""
        self._confirmation_generation += 1
        if self._refresh_after_write is not None:
            self._refresh_after_write()
            self._refresh_after_write = None
        self.client.event_callback = None
        await super().async_shutdown()
