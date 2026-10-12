"""Bounded, memory-only support evidence; never store arbitrary payloads or text."""

from __future__ import annotations

from collections import deque
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime, timedelta
import json
import logging
import math
from typing import Any
from uuid import uuid4

from mysa_client.cloud.observations import STATE_FIELDS, TELEMETRY_FIELDS, Observation

_LOGGER = logging.getLogger(__name__)
MAX_EVENTS = 500
MAX_NOISY_EVENTS = 100
MAX_EVENT_BYTES = 8192
RETENTION = timedelta(hours=24)
DEDUP_INTERVAL = timedelta(minutes=1)
_FIELDS = (
    frozenset(STATE_FIELDS)
    | frozenset(TELEMETRY_FIELDS)
    | {
        "connected",
        "target_temperature",
        "target_state",
        "display_units",
        "fan_speed",
        "temperature",
        "event_count",
        "until",
        "target_mode",
        "current_temperature",
        "current_action",
    }
)
_WORDS = (
    frozenset(
        {
            "S",
            "R",
            "L",
            "REC",
            "legacy",
            "homekit",
            "cloud",
            "modern",
            "unknown",
            "reported",
            "stored",
            "accepted",
            "ambiguous",
            "failed",
            "confirmed",
            "unchanged",
            "submitted",
            "superseded",
            "expired",
            "pending",
            "request",
            "command",
            "route",
            "source",
            "observation",
            "backoff",
            "retry",
            "recovery",
            "failure",
            "fallback",
            "readback",
            "connection",
            "reconcile",
            "state",
            "settings",
            "operation",
            "records",
            "caps",
            "firmware",
            "hold",
            "usage",
            "live",
            "schedules_v1",
            "schedules_v2",
            "GET",
            "POST",
            "PUT",
            "DELETE",
            "PATCH",
            "C",
            "F",
            "heat",
            "cool",
            "off",
            "auto",
            "on",
            "MysaAuthError",
            "MysaApiError",
            "MysaNotFoundError",
            "MysaConnectionError",
            "MysaRateLimitError",
            "TimeoutError",
            "CancelledError",
            "connection_error",
            "request_failed",
            "authentication_failed",
            "rate_limited",
            "not_found",
            "request_rejected",
            "cancelled",
            "unexpected_error",
            "invalid_clock",
            "stale",
            "missing_timestamp",
            "before_submission",
            "value_mismatch",
            "unusable",
            "device_report",
            "configuration_storage",
            "none",
            "response_lost",
            "late_response",
            "validation_failed",
            "auth_refresh",
            "count",
            "age",
            "size",
            "noise",
            "capture_failed",
            "truncated",
            "redacted",
            "set_state",
            "set_settings",
            "local_write",
            "cloud_write",
            "other_write",
            "set_hold",
            "resume_schedule",
            "schedule_write",
            "firmware_update",
            "identify",
            "read",
            "write",
            "unavailable",
            "available",
            "physical_application_unverified",
        }
    )
    | _FIELDS
)
_EVENT_KEYS = frozenset(
    {
        "at",
        "event",
        "command_id",
        "device",
        "operation",
        "status",
        "transport",
        "route",
        "method",
        "host",
        "duration_ms",
        "http_status",
        "error",
        "detail",
        "requested",
        "baseline",
        "observations",
        "source",
        "field",
        "reason",
        "evidence",
        "delay_seconds",
        "attempt",
        "active",
        "truncated",
        "superseded_by",
    }
)
_OBS_KEYS = frozenset(
    {"value", "source", "kind", "observed_at", "fetched_at", "clock_valid"}
)
_CURRENT: ContextVar[tuple[str, str | None] | None] = ContextVar(
    "mysa_capture", default=None
)


def utcnow() -> datetime:
    """Use UTC regardless of the host timezone."""
    return datetime.now(UTC)


def error_category(error: BaseException | None) -> str:
    """Classify without ever formatting an exception or its arguments."""
    from mysa_client.cloud import (
        MysaApiError,
        MysaAuthError,
        MysaConnectionError,
        MysaNotFoundError,
        MysaRateLimitError,
    )
    from mysa_client.homekit.exceptions import (
        HomeKitAuthenticationError,
        HomeKitConnectionError,
        HomeKitWriteRejectedError,
    )

    if isinstance(error, MysaAuthError | HomeKitAuthenticationError):
        return "authentication_failed"
    if isinstance(error, MysaRateLimitError):
        return "rate_limited"
    if isinstance(error, MysaNotFoundError):
        return "not_found"
    if isinstance(error, MysaApiError | HomeKitWriteRejectedError):
        return "request_rejected"
    if isinstance(error, MysaConnectionError | HomeKitConnectionError | OSError):
        return "connection_error"
    return "unexpected_error"


def _scalar(value: Any) -> Any:
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int | float):
        return value if math.isfinite(value) and abs(value) < 1e15 else None
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    if isinstance(value, str) and value in _WORDS:
        return value
    return "redacted"


def _values(values: Any) -> dict[str, Any]:
    if not isinstance(values, Mapping):
        return {}
    return {
        key: _safe_time(values[key]) if key == "until" else _scalar(values[key])
        for key in _FIELDS
        if key in values
    }


def _safe_time(value: Any) -> Any:
    if value is None:
        return None
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value)
        return parsed.astimezone(UTC).isoformat() if parsed.tzinfo else "redacted"
    except TypeError, ValueError, OverflowError:
        return "redacted"


def _observations(values: Any) -> dict[str, Any]:
    if not isinstance(values, Mapping):
        return {}
    result = {}
    for name in _FIELDS:
        obs = values.get(name)
        if isinstance(obs, Observation):
            result[name] = {key: _scalar(getattr(obs, key)) for key in _OBS_KEYS}
        elif isinstance(obs, dict):
            result[name] = {
                key: _safe_time(v)
                if key in {"observed_at", "fetched_at"} and isinstance(v, str)
                else _scalar(v)
                for key, v in obs.items()
                if key in _OBS_KEYS
            }
    return result


class EventHistory:
    """An entry-scoped capture with bounded payloads and noisy-event admission."""

    def __init__(self) -> None:
        """Start a fresh capture; no timers, disk writes, or network requests."""
        self._events: deque[tuple[datetime, bool, dict[str, Any]]] = deque()
        self._devices: dict[str, str] = {}
        self._dedup: dict[str, datetime] = {}
        self.started_at = utcnow()
        self.evicted = 0
        self.suppressed = 0
        self.dropped = 0
        self.closed = False

    def label(self, device_id: str) -> str:
        """Keep distinct devices distinct without retaining IDs in any event."""
        if self.closed:
            return "unknown"
        if device_id not in self._devices and len(self._devices) < MAX_EVENTS:
            self._devices[device_id] = f"thermostat_{len(self._devices) + 1}"
        return self._devices.get(device_id, "unknown")

    @staticmethod
    def command_id() -> str:
        """Reuse a router's correlation ID for its cloud fallback."""
        current = _CURRENT.get()
        return current[0] if current else uuid4().hex

    @contextmanager
    def command(self, command_id: str, device_id: str | None) -> Iterator[None]:
        """Correlate awaited I/O without affecting concurrent commands."""
        token = _CURRENT.set((command_id, device_id))
        try:
            yield
        finally:
            _CURRENT.reset(token)

    @contextmanager
    def detached(self) -> Iterator[None]:
        """Background polling must not inherit an originating write's context."""
        token = _CURRENT.set(None)
        try:
            yield
        finally:
            _CURRENT.reset(token)

    def _prune(self, now: datetime) -> None:
        while self._events and now - self._events[0][0] > RETENTION:
            self._events.popleft()
            self.evicted += 1
        self._dedup = {
            key: at for key, at in self._dedup.items() if now - at < DEDUP_INTERVAL
        }

    def _sanitize(self, event: Mapping[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key in _EVENT_KEYS:
            if key not in event:
                continue
            value = event[key]
            if key in {"command_id", "superseded_by"}:
                result[key] = (
                    value
                    if isinstance(value, str)
                    and len(value) == 32
                    and all(c in "0123456789abcdef" for c in value)
                    else "redacted"
                )
            elif key == "device":
                result[key] = value if value in self._devices.values() else "unknown"
            elif key == "route":
                # Only the protocol's finite route vocabulary can pass.
                from mysa_client.cloud.trace import route_template

                result[key] = (
                    route_template(value) if isinstance(value, str) else "unknown"
                )
            elif key == "requested":
                result[key] = _values(value)
            elif key in {"baseline", "observations"}:
                result[key] = _observations(value)
            elif key == "at" and isinstance(value, str):
                result[key] = _safe_time(value)
            else:
                result[key] = _scalar(value)
        return result

    def record(
        self,
        event: str,
        *,
        device_id: str | None = None,
        noisy: bool = False,
        **fields: Any,
    ) -> None:
        """Sanitize before buffering/logging; capture failure never breaks control."""
        if self.closed:
            return
        try:
            now = utcnow()
            self._prune(now)
            current = _CURRENT.get()
            if current:
                fields.setdefault("command_id", current[0])
                device_id = device_id or current[1]
            if device_id:
                fields["device"] = self.label(device_id)
            safe = self._sanitize({**fields, "event": event, "at": now})
            encoded = json.dumps(safe, sort_keys=True, allow_nan=False)
            if len(encoded.encode()) > MAX_EVENT_BYTES:
                for key in ("baseline", "observations", "requested"):
                    safe.pop(key, None)
                safe["truncated"] = True
            if noisy:
                signature = json.dumps(
                    {
                        k: v
                        for k, v in safe.items()
                        if k
                        not in {"at", "duration_ms", "observations", "delay_seconds"}
                    },
                    sort_keys=True,
                )
                if (
                    signature in self._dedup
                    or sum(item[1] for item in self._events) >= MAX_NOISY_EVENTS
                ):
                    self.suppressed += 1
                    return
                if len(self._dedup) >= MAX_NOISY_EVENTS:
                    self._dedup.pop(next(iter(self._dedup)))
                self._dedup[signature] = now
            while len(self._events) >= MAX_EVENTS:
                self._events.popleft()
                self.evicted += 1
            self._events.append((now, noisy, safe))
            _LOGGER.debug("Mysa event %s", json.dumps(safe, sort_keys=True))
        except Exception:  # noqa: BLE001 - optional evidence must never break control
            self.dropped += 1

    def request_event(self, event: str, fields: dict[str, Any]) -> None:
        """Receive protocol metadata, never a body, URL, header or exception text."""
        # Successful routine polling is deliberately omitted. Command I/O is kept.
        if (
            event == "request"
            and not _CURRENT.get()
            and fields.get("status") == "accepted"
        ):
            return
        self.record(event, noisy=_CURRENT.get() is None, **fields)

    def export(self) -> dict[str, Any]:
        """Return a detached JSON tree with a second sanitization pass."""
        self._prune(utcnow())
        events = [self._sanitize(event) for _, _, event in self._events]
        return {
            "started_at": self.started_at.isoformat(),
            "retained_from": events[0]["at"] if events else None,
            "retained_to": events[-1]["at"] if events else None,
            "limits": {
                "events": MAX_EVENTS,
                "retention_seconds": RETENTION.total_seconds(),
                "event_bytes": MAX_EVENT_BYTES,
                "noisy_events": MAX_NOISY_EVENTS,
            },
            "eviction_count": self.evicted,
            "suppressed_count": self.suppressed,
            "capture_failure_count": self.dropped,
            "confirmation_means": "device_report; physical application requires independent observation",
            "events": events,
        }

    def close(self) -> None:
        """Release the capture and all identity/dedup state on unload."""
        self.closed = True
        self._events.clear()
        self._devices.clear()
        self._dedup.clear()


_SNAPSHOT_KEYS = frozenset(
    {
        "hardware",
        "features",
        "interface",
        "smart",
        "connectivity",
        "climateControl",
        "internalTemperatureSensor",
        "humiditySensor",
        "ambientLightSensor",
        "proximitySensor",
        "userControllable",
        "validValues",
        "minValue",
        "maxValue",
        "constraints",
        "unit",
        "fanMode",
        "fanOnly",
        "fanSpeed",
        "advancedConfig",
        "stages",
        "fanRuntimePeriod",
        "minSetpointDelta",
        "temperatureOffset",
        "scheduling",
        "matter",
        "wifi",
        "bluetooth",
        "communication",
        "sensing",
        "baseboardHeating",
        "cloudEarlyOn",
        "earlyOn",
        "enableAutoMode",
        "energyReporting",
        "externalProbe",
        "externalTemperatureProbeConnector",
        "internal",
        "intensityMode",
        "maxCurrent",
        "misc",
        "power",
        "sensors",
        "trackingSensor",
        "type",
        "wattage",
        "wakeOnApproach",
        "latestTelemetry",
        "reading",
        "isConnected",
        "timestampEstimated",
        "effectiveSetpoint",
        "coreTemperature",
        "rawTemperature",
        "instantLoad",
        "onTime",
        "powerConsumed",
        "voltage",
        "diagnostics",
        "freeHeap",
        "installedVersion",
        "allowedVersion",
        "installed_version",
        "available_version",
        "heater_watts",
        "use_cloud_runtime",
        "cloud_enabled",
        "allow_firmware_install",
        "live_mode",
        "scan_interval",
        "HeatingStage1",
        "HeatingStage2",
        "CoolingStage1",
        "CoolingStage2",
        "AuxHeat",
    }
) | (
    frozenset(
        {
            "system",
            "model",
            "Model",
            "fw",
            "firmware",
            "manufacturer",
            "fields",
            "value",
            "min_value",
            "max_value",
            "min_step",
            "valid_values",
            "temperature",
            "humidity",
            "reported",
            "desired",
            "timestamp",
            "t",
            "v",
            "version",
            "data",
            "state",
            "targetHeat",
            "targetCool",
            "targetAuto",
            "modes",
            "physicalInterface",
            "tracking",
            "bbConfig",
            "hvacConfig",
            "identity",
            "drState",
            "telemetry",
            "setpoint",
            "mode",
            "lockoutMin",
            "lockoutMax",
            "format",
            "lockout",
            "activeIntensity",
            "idleIntensity",
            "ambientOffset",
            "controlType",
            "connected",
            "lastConnected",
            "lastReading",
            "sensor",
            "sensors",
            "heat",
            "cool",
            "auto",
            "range",
            "min",
            "max",
            "step",
            "Format",
            "DutyCycleOpt",
            "MinSetpoint",
            "MaxSetpoint",
            "MaxBrightness",
            "MinBrightness",
            "ButtonState",
            "dutyCycle",
            "roomTemperature",
            "currentTemperature",
            "update",
            "latest",
            "installed",
            "current",
            "available",
            "release_notes",
            "release_url",
            "floorTemperature",
            "brightness",
            "units",
            "lockoutValues",
            "controlTypes",
            "fan",
            "fan_mode",
            "hvacState",
        }
    )
    | _FIELDS
    | frozenset(wire for _, wire in STATE_FIELDS.values())
)


def redact_snapshot(value: Any) -> Any:
    """Conservatively retain known structural keys, numbers and model/version text.

    Unknown server fields, free text and dynamic keys are intentionally omitted.
    This is separate from the event schema so a future payload cannot expand it.
    """
    import re

    if isinstance(value, dict):
        return {k: redact_snapshot(v) for k, v in value.items() if k in _SNAPSHOT_KEYS}
    if isinstance(value, list | tuple):
        return [redact_snapshot(v) for v in value]
    if isinstance(value, str):
        if value in _WORDS | {
            "celsius",
            "fahrenheit",
            "Locked",
            "Unlocked",
            "Mysa",
        } or re.fullmatch(
            r"(?:BB|AC|IF|INF|ST|CS)-V\d+-\d+(?:-L)?|\d+(?:\.\d+){1,3}", value
        ):
            return value
        return "**REDACTED**"
    return _scalar(value)
