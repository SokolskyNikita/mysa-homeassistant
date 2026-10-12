"""Diagnostics, with tokens, keys, addresses and account IDs redacted."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import REDACTED, async_redact_data
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .const import (
    CONF_PAIRING,
    CONF_REFRESH_TOKEN,
    CONF_USER_ID,
    VERSION,
)
from .data import MysaConfigEntry
from .event_history import redact_snapshot

TO_REDACT = {
    CONF_REFRESH_TOKEN,
    CONF_USER_ID,
    CONF_EMAIL,
    CONF_PASSWORD,
    "iOSDeviceLTSK",
    "iOSDeviceLTPK",
    "AccessoryLTPK",
    "iOSPairingId",
    "AccessoryPairingID",
    "deviceId",
    "Id",
    "Device",
    "AccessoryIP",
    "AccessoryIPs",
    "serial",
    "Home",
    "Owner",
    "AllowedUsers",
    "homeId",
    "home_id",
    "Name",
    "name",
}


def _scrub_device_ids(data: Any, fragments: tuple[str, ...]) -> Any:
    """Redact any string that still contains a thermostat's ID or MAC.

    Its ID is the Wi-Fi MAC, and names like "Mysa-abcdef" carry the end of it,
    which with the vendor prefix gives the whole ID away.
    """
    if isinstance(data, dict):
        return {
            _scrub_device_ids(key, fragments): _scrub_device_ids(value, fragments)
            for key, value in data.items()
        }
    if isinstance(data, list):
        return [_scrub_device_ids(value, fragments) for value in data]
    if isinstance(data, str):
        flat = data.lower().replace(":", "").replace("-", "")
        if any(fragment in flat for fragment in fragments):
            return REDACTED
    return data


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: MysaConfigEntry
) -> dict[str, Any]:
    """Everything useful for supporting a new model, nothing personal."""
    runtime = entry.runtime_data
    thermostats = []
    for thermostat in runtime.thermostats.values():
        local = thermostat.local
        cloud = thermostat.cloud
        item: dict[str, Any] = {
            "device": runtime.history.label(thermostat.device_id),
            "model": redact_snapshot(thermostat.model),
            "support_status": "experimental_cloud"
            if thermostat.basic_cloud_only
            else "existing",
            "model_family": thermostat.family,
            "sensor_only": thermostat.is_climate_sensor,
            "firmware": redact_snapshot(thermostat.firmware),
            "connection_path": thermostat.connection_path,
            "supported_modes": [m.name for m in thermostat.supported_modes],
            "setpoint_range": thermostat.setpoint_range,
            "heater_watts": thermostat.heater_watts,
            "runtime_source": thermostat.runtime_source,
        }
        if local is not None:
            item["homekit"] = redact_snapshot(local.diagnostics())
        if cloud is not None and cloud.data is not None:
            dev = thermostat.device_id
            state = cloud.data.states.get(dev)
            caps = cloud.data.capabilities.get(dev)
            record = cloud.data.records.get(dev)
            firmware = cloud.data.firmware.get(dev)
            item["cloud"] = {
                "state": redact_snapshot(async_redact_data(state.raw, TO_REDACT))
                if state
                else None,
                "capabilities": redact_snapshot(async_redact_data(caps.raw, TO_REDACT))
                if caps
                else None,
                "record": redact_snapshot(async_redact_data(record.raw, TO_REDACT))
                if record
                else None,
                "firmware": redact_snapshot(asdict(firmware)) if firmware else None,
                "hold": thermostat.hold is not None,
                "sources": {
                    source: {
                        **health,
                        "partial_errors": {
                            runtime.history.label(dev): "missing_or_invalid_device"
                            for dev in health.get("partial_errors", {})
                        },
                    }
                    for source, health in cloud.source_health.items()
                },
                "observations": {
                    name: {
                        "source": obs.source,
                        "kind": obs.kind,
                        "observed_at": obs.observed_at.isoformat()
                        if obs.observed_at
                        else None,
                        "fetched_at": obs.fetched_at.isoformat(),
                        "age_seconds": (
                            dt_util.utcnow() - obs.observed_at
                        ).total_seconds()
                        if obs.observed_at
                        else None,
                        "fresh": obs.fresh(dt_util.utcnow()),
                    }
                    for name, obs in state.observations.items()
                }
                if state
                else {},
                "commands": {
                    name: {
                        "command_id": command.correlation_id,
                        "requested": redact_snapshot(command.expected),
                        "status": command.status,
                        "submitted_at": command.submitted_at.isoformat(),
                        "expires_at": command.expires_at.isoformat(),
                        "confirmation": command.confirmation,
                    }
                    for name, command in cloud.pending.get(dev, {}).items()
                },
            }
        thermostats.append(item)

    fragments = tuple(
        part
        for thermostat in runtime.thermostats.values()
        for part in (thermostat.device_id.lower(), thermostat.device_id.lower()[-6:])
    )
    diagnostics = {
        "integration_version": VERSION,
        "entry": {
            "options": redact_snapshot(dict(entry.options)),
            "signed_in": bool(entry.data.get(CONF_REFRESH_TOKEN)),
            "password_saved": CONF_PASSWORD in entry.data,
            "cloud_enabled": runtime.cloud_enabled,
            "cloud_last_update_success": (
                runtime.cloud.last_update_success if runtime.cloud else None
            ),
            "cloud_poll_seconds": (
                runtime.cloud.update_interval.total_seconds()
                if runtime.cloud and runtime.cloud.update_interval
                else None
            ),
            "cloud_fallback_count": len(runtime.cloud.fallback) if runtime.cloud else 0,
        },
        "subentries": [
            {
                "title": "**REDACTED**",
                "data": redact_snapshot(
                    async_redact_data(
                        {
                            **{k: v for k, v in sub.data.items() if k != CONF_PAIRING},
                            CONF_PAIRING: async_redact_data(
                                dict(sub.data.get(CONF_PAIRING) or {}), TO_REDACT
                            ),
                        },
                        TO_REDACT | {"device_id"},
                    )
                ),
            }
            for sub in entry.subentries.values()
        ],
        "thermostats": thermostats,
        "event_history": runtime.history.export(),
    }
    scrubbed: dict[str, Any] = _scrub_device_ids(diagnostics, fragments)
    return scrubbed
