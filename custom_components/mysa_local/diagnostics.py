"""Diagnostics, with tokens, keys, addresses and account IDs redacted."""

from __future__ import annotations

from dataclasses import asdict
import json
from typing import Any

from aiohomekit.model.characteristics import CharacteristicsTypes
from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_EMAIL
from homeassistant.core import HomeAssistant

from .const import (
    CONF_PAIRING,
    CONF_REFRESH_TOKEN,
    CONF_USER_ID,
    MAC_CHAR_UUID,
    VERSION,
)
from .data import MysaConfigEntry

TO_REDACT = {
    CONF_REFRESH_TOKEN,
    CONF_USER_ID,
    CONF_EMAIL,
    "iOSDeviceLTSK",
    "iOSDeviceLTPK",
    "AccessoryLTPK",
    "iOSPairingId",
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


# Characteristic values that identify the unit: serial, MAC, setup ID.
_PRIVATE_CHARS = {
    CharacteristicsTypes.SERIAL_NUMBER,
    MAC_CHAR_UUID,
    "00000220-0000-1000-8000-0026BB765291",
}


def _redact_accessories(accessories: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for accessory in accessories:
        for service in accessory.get("services", []):
            for char in service.get("characteristics", []):
                if (
                    str(char.get("type", "")).upper() in _PRIVATE_CHARS
                    and "value" in char
                ):
                    char["value"] = "**REDACTED**"
    return accessories


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
            "model": thermostat.model,
            "firmware": thermostat.firmware,
            "connection_path": thermostat.connection_path,
            "supported_modes": [m.name for m in thermostat.supported_modes],
            "setpoint_range": thermostat.setpoint_range,
            "heater_watts": thermostat.heater_watts,
            "runtime_source": thermostat.runtime_source,
        }
        if local is not None:
            item["homekit"] = {
                "available": local.available,
                "last_error": local.last_error,
                "info": async_redact_data(asdict(local.info), TO_REDACT | {"mac"}),
                "characteristics": {
                    ctype: {
                        "aid": meta.aid,
                        "iid": meta.iid,
                        "min": meta.min_value,
                        "max": meta.max_value,
                        "step": meta.min_step,
                        "valid": meta.valid_values,
                        "value": local.values.get(ctype),
                    }
                    for ctype, meta in local.chars.items()
                },
                "accessories": (
                    _redact_accessories(
                        json.loads(json.dumps(local.pairing.accessories.serialize()))
                    )
                    if local.pairing.accessories is not None
                    else None
                ),
            }
        if cloud is not None and cloud.data is not None:
            dev = thermostat.device_id
            state = cloud.data.states.get(dev)
            caps = cloud.data.capabilities.get(dev)
            record = cloud.data.records.get(dev)
            firmware = cloud.data.firmware.get(dev)
            item["cloud"] = {
                "state": async_redact_data(state.raw, TO_REDACT) if state else None,
                "capabilities": caps.raw if caps else None,
                "record": async_redact_data(record.raw, TO_REDACT) if record else None,
                "firmware": asdict(firmware) if firmware else None,
                "hold": thermostat.hold is not None,
            }
        thermostats.append(item)

    return {
        "integration_version": VERSION,
        "entry": {
            "options": dict(entry.options),
            "signed_in": bool(entry.data.get(CONF_REFRESH_TOKEN)),
            "cloud_enabled": runtime.cloud_enabled,
            "cloud_last_update_success": (
                runtime.cloud.last_update_success if runtime.cloud else None
            ),
        },
        "subentries": [
            {
                "title": "**REDACTED**",
                "data": async_redact_data(
                    {
                        **{k: v for k, v in sub.data.items() if k != CONF_PAIRING},
                        CONF_PAIRING: async_redact_data(
                            dict(sub.data.get(CONF_PAIRING) or {}), TO_REDACT
                        ),
                    },
                    TO_REDACT | {"device_id"},
                ),
            }
            for sub in entry.subentries.values()
        ],
        "thermostats": thermostats,
    }
