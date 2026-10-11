"""Polling Mysa's cloud for every thermostat on the account."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterable, Mapping
import contextlib
import copy
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

from .api import (
    Capabilities,
    DeviceRecord,
    DeviceState,
    FirmwareInfo,
    MysaAuthError,
    MysaClient,
    MysaConnectionError,
    MysaError,
    MysaNotFoundError,
    MysaRateLimitError,
)
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
        live_mode: bool = False,
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
        self.client = client
        self.device_ids: set[str] = set(device_ids)
        self.usage_device_ids: set[str] = set(usage_device_ids)
        self.live_mode = live_mode
        self._refresh_lock = asyncio.Lock()
        self._write_lock = asyncio.Lock()
        self._last_write = 0.0
        self._due: dict[str, float] = {}
        self._refresh_after_write: CALLBACK_TYPE | None = None
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
        LOGGER.debug(
            "Cloud fallback %s for %s; polling every %s",
            "on" if active else "off",
            device_id,
            self._normal_interval(),
        )
        self.update_interval = self._normal_interval()
        if active:
            self.hass.async_create_task(self.async_request_refresh())

    def _fallback_homes(self, records: Mapping[str, DeviceRecord]) -> set[str]:
        homes = set()
        for dev in self.fallback:
            record = records.get(dev)
            if record is not None and record.home_id:
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
                    translation_placeholders={"error": str(err)},
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

        if self._is_due("records") or any(dev not in data.records for dev in ids):
            data.records = await self.client.get_devices()
            self._mark_done("records", CLOUD_RECORDS_INTERVAL)

        for home in self._fallback_homes(data.records) if self.live_mode else ():
            if self._is_due(f"live:{home}"):
                await self._best_effort(self._start_live_mode, data, home)

        data.states = await self.client.get_states(ids)

        # The slower data is best effort: a failure keeps the previous values.
        for dev in ids:
            if dev not in data.records and dev not in data.states:
                continue
            if dev not in data.capabilities or self._is_due(f"caps:{dev}"):
                await self._best_effort(self._fetch_capabilities, data, dev)
            if self._is_due(f"firmware:{dev}"):
                await self._best_effort(self._fetch_firmware, data, dev)
            if self._is_due(f"hold:{dev}"):
                await self._best_effort(self._fetch_hold, data, dev)
            if dev in self.usage_device_ids and self._is_due(f"usage:{dev}"):
                await self._best_effort(self._fetch_usage, data, dev)
        return data

    async def _best_effort(self, fetch: Any, data: CloudData, dev: str) -> None:
        try:
            await fetch(data, dev)
        except MysaAuthError:
            raise
        except MysaError as err:
            LOGGER.debug("Optional cloud read for %s failed: %s", dev, err)

    async def _start_live_mode(self, data: CloudData, home: str) -> None:
        streamed = await self.client.start_live_mode(home, CLOUD_LIVE_MODE_TIMEOUT)
        LOGGER.debug("Live mode for home %s: %s", home, streamed)
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

    def state(self, device_id: str) -> DeviceState | None:
        """Latest state of one thermostat, if the cloud has it."""
        return self.data.states.get(device_id) if self.data else None

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
            self.data.states[device_id] = state
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
                raise translated_error("cloud_write_failed", error=str(err)) from err
            except MysaConnectionError as err:
                raise translated_error("cloud_unavailable", error=str(err)) from err
            except MysaError as err:
                raise translated_error("cloud_write_failed", error=str(err)) from err
            finally:
                self._last_write = time.monotonic()
        self._schedule_refresh_after_write()

    @callback
    def _schedule_refresh_after_write(self) -> None:
        """Re-read shortly after a write; the thermostat confirms within ~1 s."""
        if self._refresh_after_write is not None:
            self._refresh_after_write()

        @callback
        def _refresh(_now: datetime) -> None:
            # Not the debounced request_refresh: its cooldown would delay the
            # confirmation of back-to-back writes by up to 10 s.
            self._refresh_after_write = None
            self.config_entry.async_create_background_task(
                self.hass, self.async_refresh(), f"{DOMAIN} refresh after write"
            )

        self._refresh_after_write = async_call_later(
            self.hass, CLOUD_REFRESH_AFTER_WRITE, _refresh
        )

    async def async_update_state(
        self, device_id: str, changes: Mapping[str, Mapping[str, Any]]
    ) -> None:
        """Write state sections through the cloud."""
        await self._async_spaced(lambda: self.client.update_state(device_id, changes))
        # Show the change now; the re-read 2 s later confirms or corrects it.
        if self.data is not None and (state := self.data.states.get(device_id)):
            raw = copy.deepcopy(state.raw)
            for section, fields in changes.items():
                node = raw.setdefault(section, {})
                for part in ("desired", "reported"):
                    node.setdefault(part, {}).update(fields)
            self.data.states[device_id] = DeviceState.from_api(device_id, raw)
            self.async_update_listeners()

    async def async_update_settings(
        self, device_id: str, fields: Mapping[str, Any]
    ) -> None:
        """Write settings-record fields through the cloud."""
        await self._async_spaced(lambda: self.client.update_settings(device_id, fields))
        self.mark_stale("records")
        if self.data is not None and (record := self.data.records.get(device_id)):
            self.data.records[device_id] = DeviceRecord.from_api(
                device_id, {**record.raw, **fields}
            )
            self.async_update_listeners()

    async def async_call(
        self, call: Callable[[], Awaitable[Any]], *, stale: Iterable[str] = ()
    ) -> None:
        """Run any other cloud write (holds, schedules, firmware) the same way."""
        await self._async_spaced(call)
        for what in stale:
            self.mark_stale(what)

    async def async_shutdown(self) -> None:
        """Cancel the pending after-write refresh."""
        if self._refresh_after_write is not None:
            self._refresh_after_write()
            self._refresh_after_write = None
        await super().async_shutdown()
