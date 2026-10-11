"""Async client for Mysa's HTTPS API (mysa-backend.mysa.cloud)."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
import json
import logging
from typing import Any

import aiohttp

from .auth import MysaAuth
from .const import API_BASE, APP_VERSION, REQUEST_TIMEOUT, WRITE_SOURCE
from .exceptions import (
    MysaApiError,
    MysaAuthError,
    MysaConnectionError,
    MysaNotFoundError,
    MysaRateLimitError,
)
from .models import (
    Bootstrap,
    Capabilities,
    DeviceRecord,
    DeviceState,
    FirmwareInfo,
    StateChange,
    UsageDay,
)

_LOGGER = logging.getLogger(__name__)


def iso_utc(value: datetime) -> str:
    """Format like JavaScript's toISOString(), which Mysa's API expects."""
    value = value.astimezone(UTC)
    return value.strftime("%Y-%m-%dT%H:%M:%S.") + f"{value.microsecond // 1000:03d}Z"


class MysaClient:
    """Thin wrapper over the REST endpoints the Mysa app uses."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        auth: MysaAuth,
        *,
        user_agent: str,
        base_url: str = API_BASE,
    ) -> None:
        """Use an existing aiohttp session and an authenticated MysaAuth."""
        self._session = session
        self.auth = auth
        self._user_agent = user_agent
        self._base_url = base_url

    async def _request(
        self,
        method: str,
        path: str,
        *,
        body: Any = None,
        params: Mapping[str, str | int] | None = None,
        _retry_auth: bool = True,
    ) -> Any:
        token = await self.auth.async_get_id_token()
        headers = {
            "authorization": token,
            "accept": "application/json",
            "user-agent": self._user_agent,
        }
        if body is not None:
            headers["content-type"] = "application/json"
        url = f"{self._base_url}{path}"
        try:
            async with self._session.request(
                method,
                url,
                params=params,
                data=json.dumps(body) if body is not None else None,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
            ) as resp:
                status = resp.status
                text = await resp.text()
                retry_after = resp.headers.get("Retry-After")
        except (TimeoutError, aiohttp.ClientError) as err:
            msg = f"{method} {path} failed: {err}"
            raise MysaConnectionError(msg) from err

        if status in (401, 403):
            if _retry_auth:
                await self.auth.async_get_id_token(force_refresh=True)
                return await self._request(
                    method, path, body=body, params=params, _retry_auth=False
                )
            msg = f"{method} {path} was refused ({status})"
            raise MysaAuthError(msg)
        if status == 404:
            raise MysaNotFoundError(f"{method} {path}: {text[:200]}")
        if status == 429:
            try:
                wait = float(retry_after) if retry_after else None
            except ValueError:
                wait = None
            msg = f"{method} {path}: rate limited"
            raise MysaRateLimitError(msg, wait)
        if status >= 500:
            msg = f"{method} {path} returned {status}"
            raise MysaConnectionError(msg)
        if not 200 <= status < 300:
            msg = f"{method} {path} returned {status}: {text[:200]}"
            raise MysaApiError(msg, status)
        if not text:
            return None
        try:
            return json.loads(text)
        except ValueError:
            return text

    # ---- account -------------------------------------------------------

    async def bootstrap(self) -> Bootstrap:
        """Who's signed in, their homes and their thermostats."""
        data = await self._request(
            "GET", "/mobile/bootstrap", params={"versionNumber": APP_VERSION}
        )
        if not isinstance(data, dict):
            msg = "Unexpected bootstrap response"
            raise MysaApiError(msg)
        return Bootstrap.from_api(data)

    async def get_devices(self) -> dict[str, DeviceRecord]:
        """Settings records of every thermostat on the account."""
        data = await self._request("GET", "/devices")
        devices = data.get("DevicesObj") if isinstance(data, dict) else None
        if not isinstance(devices, dict):
            msg = "Unexpected /devices response"
            raise MysaApiError(msg)
        return {
            dev_id: DeviceRecord.from_api(dev_id, record)
            for dev_id, record in devices.items()
            if isinstance(record, dict)
        }

    # ---- state ---------------------------------------------------------

    async def get_states(self, device_ids: Iterable[str]) -> dict[str, DeviceState]:
        """Current state of several thermostats in one request."""
        ids = list(device_ids)
        if not ids:
            return {}
        data = await self._request("POST", "/state/batch", body={"deviceIds": ids})
        if not isinstance(data, dict):
            msg = "Unexpected /state/batch response"
            raise MysaApiError(msg)
        states = {}
        for dev_id, item in data.items():
            if isinstance(item, dict) and isinstance(item.get("data"), dict):
                states[dev_id] = DeviceState.from_api(dev_id, item["data"])
        return states

    async def get_state(self, device_id: str) -> DeviceState:
        """Current state of one thermostat."""
        data = await self._request("GET", f"/state/{device_id}")
        if not isinstance(data, dict):
            msg = "Unexpected state response"
            raise MysaApiError(msg)
        return DeviceState.from_api(device_id, data)

    async def update_state(
        self, device_id: str, changes: Mapping[str, Mapping[str, Any]]
    ) -> None:
        """Change state sections, e.g. {"targetHeat": {"setpoint": 21.5}}."""
        body: dict[str, Any] = {"source": WRITE_SOURCE}
        body.update({section: dict(fields) for section, fields in changes.items()})
        await self._request("POST", f"/state/{device_id}/update", body=body)

    async def update_settings(self, device_id: str, fields: Mapping[str, Any]) -> None:
        """Change fields of the settings record (Format, DutyCycleOpt, Name, ...)."""
        # The trailing slash matches the app; without it the route differs.
        await self._request("POST", f"/devices/{device_id}/", body=dict(fields))

    async def get_capabilities(self, device_id: str) -> Capabilities:
        """What this model supports."""
        data = await self._request(
            "GET",
            f"/capabilities/{device_id}",
            params={
                "includeLegacyBaseboard": "true",
                "includeLegacyAc": "true",
                "includeLegacyInfloor": "true",
            },
        )
        if not isinstance(data, dict):
            msg = "Unexpected capabilities response"
            raise MysaApiError(msg)
        return Capabilities.from_api(data)

    # ---- firmware ------------------------------------------------------

    async def get_firmware_info(self, device_id: str) -> FirmwareInfo:
        """Whether Mysa offers a firmware update for this thermostat."""
        data = await self._request("GET", f"/devices/update_available/{device_id}")
        if not isinstance(data, dict):
            msg = "Unexpected update_available response"
            raise MysaApiError(msg)
        return FirmwareInfo.from_api(data)

    async def start_firmware_update(self, device_id: str) -> None:
        """Ask Mysa to push the offered firmware. Irreversible."""
        await self._request("POST", f"/devices/{device_id}/firmware-update")

    # ---- history -------------------------------------------------------

    async def get_usage(
        self, device_id: str, time_zone: str, start: datetime, end: datetime
    ) -> list[UsageDay]:
        """Heating on-time per local day (30-minute buckets inside)."""
        data = await self._request(
            "POST",
            f"/telemetry/usage/{device_id}",
            body={
                "timezone": time_zone,
                "start": int(start.timestamp()),
                "end": int(end.timestamp()),
                "summary": {"oH1": "sum", "t": "average"},
                "aggregatedReadings": ["oH1", "t"],
            },
        )
        if not isinstance(data, list):
            return []
        days = [UsageDay.from_api(day) for day in data if isinstance(day, dict)]
        return [day for day in days if day is not None]

    async def get_state_history(
        self, device_id: str, start: datetime, end: datetime
    ) -> list[StateChange]:
        """Who changed what, oldest first."""
        data = await self._request(
            "GET",
            f"/state/history/{device_id}",
            params={
                "startTime": int(start.timestamp()),
                "endTime": int(end.timestamp()),
            },
        )
        changes: list[StateChange] = []
        if not isinstance(data, dict):
            return changes
        for field_name, entries in data.items():
            if not isinstance(entries, list):
                continue
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                stamp = entry.get("timestamp")
                if not isinstance(stamp, int | float):
                    continue
                source = entry.get("source")
                changes.append(
                    StateChange(
                        field=field_name,
                        time=datetime.fromtimestamp(stamp, UTC),
                        value=entry.get("value"),
                        source=source if isinstance(source, str) else None,
                    )
                )
        changes.sort(key=lambda change: change.time)
        return changes

    # ---- schedules and holds ------------------------------------------

    async def get_hold(self, device_id: str) -> dict[str, Any] | None:
        """The active hold, or None when the thermostat follows its schedule."""
        try:
            data = await self._request("GET", f"/schedule/hold/{device_id}")
        except MysaNotFoundError:
            return None
        return data if isinstance(data, dict) else None

    async def set_hold(
        self,
        device_id: str,
        state: Mapping[str, Any],
        *,
        until: datetime | None = None,
    ) -> None:
        """Hold a state permanently, or until a time, overriding the schedule."""
        body: dict[str, Any] = {
            "deviceId": device_id,
            "holdType": "temporary" if until else "permanent",
            "startTime": iso_utc(datetime.now(UTC)),
            "state": dict(state),
        }
        if until is not None:
            body["endTime"] = iso_utc(until)
        await self._request("POST", "/schedule/hold", body=body)

    async def start_live_mode(self, home_id: str, seconds: int = 300) -> list[str]:
        """Have a home's thermostats report every few seconds for `seconds` s.

        Mysa's app sends this while it's open and renews it about every 4 min.
        Returns the IDs of the thermostats that switched to live mode.
        """
        data = await self._request(
            "POST", f"/mysa-legacy/stream/home/{home_id}", body={"timeout": seconds}
        )
        ids = data.get("streamedDeviceIds") if isinstance(data, dict) else None
        if not isinstance(ids, list):
            return []
        return [i for i in ids if isinstance(i, str)]

    async def resume_schedule(self, device_id: str) -> None:
        """End any hold so the thermostat follows its schedule again."""
        try:
            await self._request("DELETE", f"/schedule/hold/{device_id}")
        except MysaNotFoundError:
            _LOGGER.debug("No hold to remove on %s", device_id)

    async def get_schedules(self, home_id: str) -> list[dict[str, Any]]:
        """Every schedule in a home."""
        try:
            data = await self._request(
                "GET",
                "/schedule",
                params={
                    "homeId": home_id,
                    "includeLegacyBaseboard": "true",
                    "includeLegacyAc": "true",
                    "includeLegacyInfloor": "true",
                },
            )
        except MysaNotFoundError:
            return []
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        return []

    async def create_schedule(
        self,
        *,
        home_id: str,
        device_id: str,
        device_type: str,
        events: list[dict[str, Any]],
    ) -> str | None:
        """Create a weekly schedule for one thermostat and return its id."""
        data = await self._request(
            "POST",
            "/schedule",
            body={
                "name": f"Schedule-{device_id}",
                "deviceType": device_type,
                "homeId": home_id,
                "reusable": False,
                "deviceIds": [device_id],
                "events": events,
            },
        )
        if isinstance(data, dict):
            schedule_id = data.get("scheduleId") or data.get("id")
            return str(schedule_id) if schedule_id is not None else None
        return None

    async def delete_schedule(self, schedule_id: str, device_id: str) -> None:
        """Detach a schedule from its thermostat and delete it."""
        await self._request("DELETE", f"/schedule/{schedule_id}/attach/{device_id}")
