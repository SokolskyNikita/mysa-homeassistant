"""Async client for Mysa's HTTPS API (mysa-backend.mysa.cloud)."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import UTC, datetime, timedelta
import json
import logging
import math
import time
from typing import Any, Literal

import aiohttp

from .auth import MysaAuth
from .commands import (
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
from .const import (
    API_BASE,
    APP_VERSION,
    EXPERIMENTAL_CLOUD_FAMILIES,
    LEGACY_API_BASE,
    REQUEST_TIMEOUT,
    WIFI_ONLY_FAMILIES,
    WRITE_SOURCE,
    AcFan,
    ButtonLock,
    DutyCycleOpt,
    HeaterType,
    Mode,
    ModelFamily,
    Swing,
    model_family,
)
from .exceptions import (
    MysaApiError,
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

HOLD_START_DELAY = timedelta(seconds=10)

# What Mysa's app adds for an In-floor thermostat: a hardwood/laminate floor
# (FloorType 1) read by Mysa's own floor sensor (Tracking 3), capped at 28 °C.
_INFLOOR_DEFAULTS: dict[str, Any] = {
    "DutyCycleOpt": int(DutyCycleOpt.BB1_BASEBOARD),
    "FloorOhms": 10,
    "FloorType": 1,
    "IsMysaFloorSensor": True,
    "MaxFloorTemp": 28,
    "MaxSetpoint": 28,
    "Tracking": 3,
}


def iso_utc(value: datetime) -> str:
    """Format like JavaScript's toISOString(), which Mysa's API expects."""
    value = value.astimezone(UTC)
    return value.strftime("%Y-%m-%dT%H:%M:%S.") + f"{value.microsecond // 1000:03d}Z"


def _device_body(
    device_id: str,
    *,
    home_id: str,
    model: str,
    time_zone: str,
    name: str | None,
    fahrenheit: bool,
    heater_type: int | None,
    extra: Mapping[str, Any] | None,
    app_defaults: bool,
) -> dict[str, Any]:
    """The record Mysa's app sends when it adds a thermostat.

    With app_defaults, the 4.20.1 app's body, settings defaults included.
    Without, the 3.54.1 app's smaller body, which leaves the stored settings of
    a thermostat that was on the account before alone.
    """
    family = model_family(model)
    if family is None:
        msg = f"Unknown Mysa model {model!r}"
        raise ValueError(msg)
    if family not in WIFI_ONLY_FAMILIES:
        msg = (
            f"{model} needs a credential file over Bluetooth to join an account; "
            "adding it isn't supported"
        )
        raise ValueError(msg)
    body: dict[str, Any] = {
        "Id": device_id,
        "Name": name or device_id,
        "Model": model,
        "Home": home_id,
        "TimeZone": time_zone,
        "LastPaired": int(datetime.now(UTC).timestamp()),
    }
    if family is ModelFamily.AC:
        body["SetupRequired"] = True
    if family is ModelFamily.BASEBOARD_LITE:
        body["schedGlobalOffset"] = 0
    if app_defaults:
        body.update(
            {
                "Animation": "off",
                "ecoMode": 1,
                "Format": "fahrenheit" if fahrenheit else "celsius",
            }
        )
        if family is not ModelFamily.BASEBOARD_LITE:
            body["schedGlobalOffset"] = 1  # the app's value for a new V1/V2/In-floor
        if family is ModelFamily.INFLOOR:
            body.update(_INFLOOR_DEFAULTS)
        if heater_type is None and family is ModelFamily.BASEBOARD_V1:
            heater_type = DutyCycleOpt.BB1_BASEBOARD
        elif heater_type is None and family in {
            ModelFamily.BASEBOARD_V2,
            ModelFamily.BASEBOARD_LITE,
        }:
            heater_type = DutyCycleOpt.BB2_BASEBOARD
    if heater_type is not None:
        body["DutyCycleOpt"] = int(heater_type)
    if extra:
        body.update(extra)
    return body


class MysaClient:
    """Thin wrapper over the REST endpoints the Mysa app uses."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        auth: MysaAuth,
        *,
        user_agent: str,
        base_url: str = API_BASE,
        legacy_base_url: str = LEGACY_API_BASE,
    ) -> None:
        """Use an existing aiohttp session and an authenticated MysaAuth."""
        self._session = session
        self.auth = auth
        self._user_agent = user_agent
        self._base_url = base_url
        self._legacy_base_url = legacy_base_url
        self._fallback_until: dict[str, float] = {}
        self.source_errors: dict[str, dict[str, str]] = {}
        self.legacy_source = "R"
        self._device_models: dict[str, str | None] = {}

    async def _request(
        self,
        method: str,
        path: str,
        *,
        body: Any = None,
        params: Mapping[str, str | int] | None = None,
        base_url: str | None = None,
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
        url = f"{base_url or self._base_url}{path}"
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

        if status == 401 and _retry_auth:
            # A stale ID token: renew it and retry once. A sign-in that's really
            # gone fails in the renewal itself (MysaAuthError from Cognito).
            await self.auth.async_get_id_token(force_refresh=True)
            return await self._request(
                method,
                path,
                body=body,
                params=params,
                base_url=base_url,
                _retry_auth=False,
            )
        if status in (401, 403):
            # A 403, or a 401 with a fresh token, is about this request (a route
            # the host doesn't have, a permission), not the sign-in. Mysa's app
            # never asks to sign in again over an HTTP status either.
            msg = f"{method} {path} was refused ({status}): {text[:200]}"
            raise MysaApiError(msg, status)
        if status == 404:
            raise MysaNotFoundError(f"{method} {path}: {text[:200]}")
        if status == 429:
            try:
                wait = float(retry_after) if retry_after else None
                if wait is not None and (not math.isfinite(wait) or wait < 0):
                    wait = None
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

    async def _legacy_request(
        self, operation: str, method: str, path: str, *, body: Any = None
    ) -> Any:
        """Use only verified legacy routes; ambiguous writes are never replayed."""
        if operation not in {
            "state",
            "records",
            "settings",
            "schedules_v1",
            "schedules_v2",
        }:
            raise ValueError(f"No verified fallback for {operation}")
        use_old = time.monotonic() < self._fallback_until.get(operation, 0)
        try:
            data = await self._request(
                method,
                path,
                body=body,
                base_url=self._legacy_base_url if use_old else None,
            )
        except (MysaConnectionError, MysaApiError, MysaNotFoundError) as err:
            if operation == "schedules_v2" and isinstance(err, MysaNotFoundError):
                raise  # A device without a schedule does not imply a missing host route.
            unsupported = isinstance(err, MysaNotFoundError) or (
                isinstance(err, MysaApiError) and err.status in (403, 405, 501)
            )
            # 429 is a service limit, not evidence another host should be tried.
            if (
                use_old
                or isinstance(err, MysaRateLimitError)
                or (method != "GET" and not unsupported)
            ):
                raise
            if not unsupported and not isinstance(err, MysaConnectionError):
                raise
            self._fallback_until[operation] = time.monotonic() + 300
            data = await self._request(
                method, path, body=body, base_url=self._legacy_base_url
            )
            use_old = True
        if operation == "state":
            self.legacy_source = "L" if use_old else "R"
        return data

    async def get_legacy_states(
        self, device_ids: Iterable[str]
    ) -> dict[str, DeviceState]:
        """Legacy live state with per-field clocks and independent partial errors."""
        device_ids = list(device_ids)
        if not device_ids:
            return {}
        data = await self._legacy_request("state", "GET", "/devices/state")
        if not isinstance(data, dict) or not isinstance(
            data.get("DeviceStatesObj"), dict
        ):
            raise MysaApiError("Unexpected /devices/state response")
        states = {}
        errors = {}
        for dev in device_ids:
            item = data["DeviceStatesObj"].get(dev)
            if isinstance(item, dict):
                states[dev] = DeviceState.from_legacy(
                    dev,
                    item,
                    source=self.legacy_source,
                    model=self._device_models.get(dev),
                )
            else:
                errors[dev] = "missing_or_invalid_device"
        self.source_errors["legacy"] = errors
        return states

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
        data = await self._legacy_request("records", "GET", "/devices")
        devices = data.get("DevicesObj") if isinstance(data, dict) else None
        if not isinstance(devices, dict):
            msg = "Unexpected /devices response"
            raise MysaApiError(msg)
        if any(not isinstance(record, dict) for record in devices.values()):
            raise MysaApiError("Incomplete device records")
        self._device_models.update(
            {dev: record.get("Model") for dev, record in devices.items()}
        )
        return {
            dev_id: DeviceRecord.from_api(dev_id, record)
            for dev_id, record in devices.items()
            if isinstance(record, dict)
        }

    async def add_device(
        self,
        device_id: str,
        *,
        home_id: str,
        model: str,
        time_zone: str,
        name: str | None = None,
        fahrenheit: bool = False,
        heater_type: int | None = None,
        extra: Mapping[str, Any] | None = None,
        app_defaults: bool = True,
    ) -> bool:
        """Put a thermostat on the account by its ID (the Wi-Fi MAC).

        True when it was added, False when it was already on the account (409;
        Mysa writes nothing then). Only models that join with Wi-Fi details
        alone (Baseboard V1/V2/Lite, In-floor, AC) can be added; others raise
        ValueError before anything is sent.

        The fields in the body replace the stored ones when a thermostat comes
        back to the account. With app_defaults (Mysa's 4.20.1 app), that
        includes the settings defaults: heater type Baseboard on a V2/Lite, °C,
        and more. app_defaults=False sends the 3.54.1 app's smaller body (ID,
        name, model, home, time zone), so a returning thermostat keeps its
        settings. `heater_type` (a DutyCycleOpt or HeaterType) and `extra` are
        sent either way.

        Not tested: a thermostat on another account, which Mysa's app recognizes
        by "User is not allowed to access the specified home" in the error (the
        status code is unknown).
        """
        body = _device_body(
            device_id,
            home_id=home_id,
            model=model,
            time_zone=time_zone,
            name=name,
            fahrenheit=fahrenheit,
            heater_type=heater_type,
            extra=extra,
            app_defaults=app_defaults,
        )
        try:
            await self._request("POST", "/devices", body=body)
        except MysaApiError as err:
            if err.status == 409:
                return False
            raise
        return True

    async def replace_device(
        self,
        device_id: str,
        *,
        home_id: str,
        model: str,
        time_zone: str,
        name: str | None = None,
        fahrenheit: bool = False,
        heater_type: int | None = None,
        extra: Mapping[str, Any] | None = None,
        app_defaults: bool = True,
    ) -> None:
        """Add a thermostat, replacing the record Mysa kept for it ("purgeadd").

        Mysa's older app sent this when the user picked Overwrite after a 409.
        Settings missing from the body may be reset. Same models and body as
        add_device.
        """
        body = _device_body(
            device_id,
            home_id=home_id,
            model=model,
            time_zone=time_zone,
            name=name,
            fahrenheit=fahrenheit,
            heater_type=heater_type,
            extra=extra,
            app_defaults=app_defaults,
        )
        await self._request(
            "POST", "/devices/purgeadd", body=body, base_url=self._legacy_base_url
        )

    async def delete_device(self, device_id: str) -> None:
        """Remove a thermostat from the account.

        Mysa keeps its data, and add_device brings it back. The thermostat
        itself isn't affected. Mysa's app first takes it out of the home's
        shortcuts and deletes its schedule; this call doesn't.
        """
        await self._request("DELETE", f"/devices/{device_id}")

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
        errors = {}
        for dev_id in ids:
            item = data.get(dev_id)
            if isinstance(item, dict) and isinstance(item.get("data"), dict):
                states[dev_id] = DeviceState.from_api(dev_id, item["data"])
            else:
                errors[dev_id] = "missing_or_invalid_device"
        self.source_errors["S"] = errors
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

    async def set_temperature(
        self, device_id: str, mode: Mode | None, temperature: float
    ) -> None:
        """Set a thermostat's temperature target for the selected mode."""
        await self.update_state(device_id, temperature_changes(mode, temperature))

    async def set_mode(self, device_id: str, mode: Mode) -> None:
        """Set a thermostat's operating mode."""
        await self.update_state(device_id, mode_changes(mode))

    async def set_fan_speed(
        self, device_id: str, speed: AcFan, mode: Mode | None = None
    ) -> None:
        """Set an AC fan speed, optionally including its current mode."""
        await self.update_state(device_id, fan_speed_changes(speed, mode))

    async def set_swing(self, device_id: str, direction: str, value: Swing) -> None:
        """Set vertical or horizontal AC swing."""
        await self.update_state(device_id, swing_changes(direction, value))

    async def set_display_brightness(
        self,
        device_id: str,
        *,
        active: int,
        idle: int,
        route: Literal["S", "R", "L"] = "S",
    ) -> None:
        """Set both wake and idle screen brightness values."""
        if not 0 <= idle <= active <= 100:
            raise ValueError("Brightness must satisfy 0 <= idle <= active <= 100")
        await self._setting_route(
            device_id,
            display_brightness_changes(active, idle),
            {"MaxBrightness": active, "MinBrightness": idle},
            route,
        )

    async def set_button_lock(
        self, device_id: str, value: ButtonLock, *, route: Literal["S", "R", "L"] = "S"
    ) -> None:
        """Lock or unlock the thermostat's physical buttons."""
        if route != "S" and value not in (ButtonLock.UNLOCKED, ButtonLock.LOCKED):
            raise ValueError("Legacy settings support only locked/unlocked")
        await self._setting_route(
            device_id,
            button_lock_changes(value),
            {"ButtonState": "Locked" if int(value) == 3 else "Unlocked"},
            route,
        )

    async def set_setpoint_limit(
        self,
        device_id: str,
        value: float,
        *,
        minimum: bool,
        route: Literal["S", "R", "L"] = "S",
    ) -> None:
        """Set the minimum or maximum allowed heating setpoint."""
        await self._setting_route(
            device_id,
            setpoint_limit_changes(value, minimum=minimum),
            {"MinSetpoint" if minimum else "MaxSetpoint": value},
            route,
        )

    async def set_temperature_offset(self, device_id: str, value: float) -> None:
        """Set the room-temperature correction offset."""
        await self.update_state(device_id, temperature_offset_changes(value))

    async def update_settings(self, device_id: str, fields: Mapping[str, Any]) -> None:
        """Change fields of the settings record (Format, DutyCycleOpt, Name, ...)."""
        # Only verified semantic fields participate in automatic host fallback.
        allowed = {
            "ButtonState",
            "MaxBrightness",
            "MinBrightness",
            "MinSetpoint",
            "MaxSetpoint",
            "Format",
            "DutyCycleOpt",
        }
        if fields and set(fields) <= allowed:
            await self._legacy_request(
                "settings", "POST", f"/devices/{device_id}/", body=dict(fields)
            )
        else:
            await self._request("POST", f"/devices/{device_id}/", body=dict(fields))

    async def set_display_unit(
        self, device_id: str, unit: str, *, route: Literal["S", "R", "L"] = "R"
    ) -> None:
        """Set the thermostat's screen to Celsius or Fahrenheit."""
        settings = display_unit_settings(unit)
        await self._setting_route(
            device_id, {"physicalInterface": {"format": unit.upper()}}, settings, route
        )

    async def set_heater_type(
        self,
        device_id: str,
        heater_type: int | HeaterType,
        *,
        route: Literal["S", "R", "L"] = "R",
    ) -> None:
        """Set the heater profile in the device settings record."""
        await self._setting_route(
            device_id,
            {"bbConfig": {"controlType": int(heater_type)}},
            heater_type_settings(heater_type),
            route,
        )

    async def _setting_route(
        self,
        device_id: str,
        state: Mapping[str, Mapping[str, Any]],
        settings: Mapping[str, Any],
        route: Literal["S", "R", "L"],
    ) -> None:
        """Select one verified semantic encoder; never broadcast a command."""
        if route == "S":
            await self.update_state(device_id, state)
        elif route == "R":
            await self.update_settings(device_id, settings)
        elif route == "L":
            await self._request(
                "POST",
                f"/devices/{device_id}/",
                body=dict(settings),
                base_url=self._legacy_base_url,
            )
        else:
            raise ValueError(f"Unsupported settings route: {route}")

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
        await self._request("POST", f"/devices/{device_id}/firmware-update", body={})

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
            # Mysa refuses a start time that isn't in the future by the time
            # the request arrives ("Start time must be greater than current time").
            "startTime": iso_utc(datetime.now(UTC) + HOLD_START_DELAY),
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
        if not isinstance(data, list) or any(
            not isinstance(item, dict) for item in data
        ):
            raise MysaApiError("Malformed schedule discovery")
        for item in data:
            if (
                not isinstance(item.get("id"), str)
                or not isinstance(item.get("deviceIds"), list)
                or any(not isinstance(dev, str) for dev in item["deviceIds"])
            ):
                raise MysaApiError("Incomplete schedule discovery")
            if not isinstance(item.get("events"), list) or any(
                not isinstance(event, dict) for event in item["events"]
            ):
                raise MysaApiError("Incomplete schedule events")
        return data

    async def get_all_schedules(self, home_id: str) -> list[dict[str, Any]]:
        """Complete native discovery; no mutation is safe after an incomplete read."""
        from .schedules import native_schedule

        modern = await self.get_schedules(home_id)
        records = await self.get_devices()
        devices = {
            dev: record
            for dev, record in records.items()
            if record.home_id == home_id
            and model_family(record.model) not in EXPERIMENTAL_CLOUD_FAMILIES
        }
        if not devices:
            return modern
        data = await self._legacy_request("schedules_v1", "GET", "/schedules")
        if not isinstance(data, dict) or not isinstance(data.get("Schedules"), list):
            raise MysaApiError("Malformed legacy schedule list")
        native: dict[tuple[str, str], dict[str, Any]] = {}
        for raw in data["Schedules"]:
            if not isinstance(raw, dict) or not isinstance(raw.get("Device"), str):
                raise MysaApiError("Malformed legacy schedule entry")
            dev = raw["Device"]
            if dev in devices:
                entry = native_schedule(
                    "v1", raw, dev, devices[dev].model or "", home_id
                )
                if entry:
                    native[("v1", entry["id"])] = entry
        for dev, record in devices.items():
            try:
                raw = await self._legacy_request(
                    "schedules_v2", "GET", f"/schedules/v2/{dev}"
                )
            except MysaNotFoundError:
                continue
            if not isinstance(raw, dict):
                raise MysaApiError("Malformed legacy v2 response")
            entry = native_schedule("v2", raw, dev, record.model or "", home_id)
            if entry:
                key = ("v2", entry["id"])
                if key in native:
                    if (
                        native[key]["native"] != entry["native"]
                        or native[key]["deviceType"] != entry["deviceType"]
                    ):
                        raise MysaApiError("Conflicting shared legacy schedule")
                    native[key]["deviceIds"].append(dev)
                else:
                    native[key] = entry
        native_ids = {item["id"] for item in native.values()}
        return [item for item in modern if item["id"] not in native_ids] + list(
            native.values()
        )

    async def remove_schedule(
        self, schedule: Mapping[str, Any], device_id: str
    ) -> None:
        """Detach through the snapshot's native route, preserving other attachments."""
        from .schedules import WEEKDAYS

        origin = schedule.get("origin")
        if origin == "v1":
            await self._legacy_request(
                "schedules_v1",
                "POST",
                f"/schedules/device/update/{device_id}",
                body={
                    "Device": device_id,
                    "Model": schedule["deviceType"],
                    "ScheduledActions": {day: [] for day in WEEKDAYS},
                },
            )
        elif origin == "v2":
            await self._legacy_request(
                "schedules_v2",
                "DELETE",
                f"/schedules/v2/{schedule['id']}",
                body={"Devices": [device_id], "Model": schedule["deviceType"]},
            )
        else:
            await self.delete_schedule(str(schedule["id"]), device_id)

    async def restore_native_schedule(self, schedule: Mapping[str, Any]) -> None:
        """Restore a journal snapshot after failed cleanup; no user-facing editor."""
        raw = dict(schedule["native"])
        devices = list(schedule["deviceIds"])
        if schedule["origin"] == "v1":
            for dev in devices:
                await self._legacy_request(
                    "schedules_v1",
                    "POST",
                    f"/schedules/device/update/{dev}",
                    body={
                        "Device": dev,
                        "Model": schedule["deviceType"],
                        "ScheduledActions": raw["ScheduledActions"],
                    },
                )
        else:
            await self._legacy_request(
                "schedules_v2",
                "POST",
                "/schedules/v2/",
                body={
                    "Schedule": raw,
                    "Devices": devices,
                    "Model": schedule["deviceType"],
                },
            )

    async def create_schedule(
        self,
        *,
        home_id: str,
        device_id: str,
        device_type: str,
        events: list[dict[str, Any]],
        name: str | None = None,
        reusable: bool = False,
        device_ids: list[str] | None = None,
    ) -> str | None:
        """Create a weekly schedule and return its id.

        The optional metadata allows recovery to recreate a schedule without
        silently changing its name, reuse behavior, or attached thermostats.
        """
        data = await self._request(
            "POST",
            "/schedule",
            body={
                "name": name or f"Schedule-{device_id}",
                "deviceType": device_type,
                "homeId": home_id,
                "reusable": reusable,
                "deviceIds": device_ids or [device_id],
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

    # ---- shortcuts (Home / Away / Vacation) ----------------------------

    async def get_shortcuts(self, home_id: str) -> list[dict[str, Any]]:
        """A home's shortcuts, each with Id, DeviceActions and AllDevices."""
        data = await self._request("GET", f"/homes/{home_id}/scenes")
        scenes = data.get("Scenes") if isinstance(data, dict) else None
        if not isinstance(scenes, list):
            return []
        return [scene for scene in scenes if isinstance(scene, dict)]

    async def update_shortcut(
        self,
        shortcut_id: str,
        device_actions: Mapping[str, Any],
        *,
        all_devices: bool,
    ) -> None:
        """Replace a shortcut's per-thermostat actions."""
        await self._request(
            "PATCH",
            f"/scene/{shortcut_id}",
            body={"DeviceActions": dict(device_actions), "AllDevices": all_devices},
        )
