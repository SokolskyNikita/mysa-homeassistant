"""Shared flow identifiers, presentation, and entry helpers."""

from __future__ import annotations

from collections.abc import Mapping
import re
from typing import Any

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigSubentryData,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from custom_components.mysa_local.account import (
    ACCOUNT_OFF,
    ACCOUNT_ON,
    is_supported_model,
)
from custom_components.mysa_local.const import (
    CONF_CLOUD_RUNTIME,
    CONF_DEVICE_ID,
    CONF_HEATER_WATTS,
    CONF_MIGRATE_FROM,
    CONF_MODEL,
    CONF_PAIRING,
    DOMAIN,
    HOMEKIT_MANUFACTURER,
    SUBENTRY_THERMOSTAT,
    is_mysa_model,
)
from custom_components.mysa_local.migration import HKC_DOMAIN
from custom_components.mysa_local.protocols.homekit import (
    AccessoryInfo,
    HomeKitMalformedPinError,
)

INSECURE_CODES = {
    "00000000",
    "11111111",
    "22222222",
    "33333333",
    "44444444",
    "55555555",
    "66666666",
    "77777777",
    "88888888",
    "99999999",
    "12345678",
    "87654321",
}
CONF_PAIRING_CODE = "pairing_code"
CONF_DEVICE = "device"
CONF_ENTRY = "entry"
CONF_DEVICES = "devices"
CONF_ADD_TO_ACCOUNT = "add_to_account"
CONF_HOME = "home"


# Status text built in code (the integration ships English only).
LOCAL_TEXT: dict[bool, str] = {
    True: "paired over HomeKit",
    False: "not paired locally (Mysa cloud only)",
}


def md(text: str) -> str:
    """Escape a name or email for Markdown (lines and table cells).

    "@" becomes an HTML entity: it still shows as "@", but Home Assistant's
    Markdown no longer turns an email address into a mailto link.
    """
    for char in ("\\", "|", "*", "_", "`", "[", "]"):
        text = text.replace(char, "\\" + char)
    return text.replace("@", "&#64;")


def names_differ(ha_name: str, cloud_name: str | None) -> bool:
    """True if the Mysa app knows the thermostat by another name."""
    return bool(cloud_name) and cloud_name.casefold() != ha_name.casefold()  # type: ignore[union-attr]


def account_line(status: str | None, ha_name: str, cloud_name: str | None) -> str:
    """Status line for the Mysa account."""
    if status == ACCOUNT_ON:
        line = "✅ **Mysa account:** on your account"
        if cloud_name and names_differ(ha_name, cloud_name):
            line += f', named "{md(cloud_name)}" in the Mysa app'
        return line
    if status == ACCOUNT_OFF:
        return "⚠️ **Mysa account:** not on your account"
    return "**Mysa account:** unknown (not signed in to Mysa, or the Mysa cloud is off)"


def local_line(paired: bool) -> str:
    """Status line for the local (HomeKit) connection."""
    if paired:
        return "✅ **Local control:** paired over HomeKit"
    return "⚠️ **Local control:** not paired, so it relies on the Mysa cloud"


class InsecureCodeError(ValueError):
    """A trivial code like 11111111, which HomeKit never uses."""


class PairingPersistenceError(Exception):
    """The accessory paired, but HA could not save the recovery credentials."""

    def __init__(self) -> None:
        """Create a safe error without retaining or logging pairing secrets."""
        super().__init__("Couldn't persist the HomeKit pairing")


def format_pin(pin: str) -> str:
    """Return the code as 123-45-678, whatever spacing or dashes it was typed with.

    Only the digits count. Raises MalformedPinError unless there are exactly 8,
    and InsecureCodeError for the trivial codes HomeKit forbids.
    """
    digits = re.sub(r"\D", "", pin)
    if len(digits) != 8:
        raise HomeKitMalformedPinError(pin)
    if digits in INSECURE_CODES:
        raise InsecureCodeError(pin)
    return f"{digits[:3]}-{digits[3:5]}-{digits[5:]}"


def hub_entry(hass: HomeAssistant) -> ConfigEntry | None:
    """The single Mysa Local entry, if it exists."""
    for entry in hass.config_entries.async_entries(DOMAIN, include_ignore=False):
        return entry
    return None


def configured_hkids(entry: ConfigEntry | None) -> dict[str, str]:
    """HomeKit IDs (lowercase) of thermostats already in the entry -> subentry ID."""
    if entry is None:
        return {}
    result = {}
    for subentry in entry.subentries.values():
        pairing = subentry.data.get(CONF_PAIRING) or {}
        if hkid := pairing.get("AccessoryPairingID"):
            result[hkid.lower()] = subentry.subentry_id
    return result


def homekit_device_candidates(hass: HomeAssistant) -> dict[str, str]:
    """HomeKit Device entries that are Mysa thermostats: entry ID -> title."""
    dev_reg = dr.async_get(hass)
    candidates = {}
    for entry in hass.config_entries.async_entries(HKC_DOMAIN, include_ignore=False):
        if not entry.data.get("AccessoryPairingID"):
            continue
        for device in dr.async_entries_for_config_entry(dev_reg, entry.entry_id):
            if (
                device.manufacturer == HOMEKIT_MANUFACTURER
                or is_mysa_model(device.model)
            ) and is_supported_model(device.model):
                candidates[entry.entry_id] = entry.title
                break
    return candidates


def thermostat_subentry(
    info: AccessoryInfo,
    pairing: Mapping[str, Any] | None,
    title: str,
    migrate_from: str | None = None,
    *,
    device_id: str | None = None,
    model: str | None = None,
) -> ConfigSubentryData:
    """Build the subentry for one thermostat."""
    if device_id is None:
        hkid = (pairing or {}).get("AccessoryPairingID", "")
        device_id = info.device_id or "hk" + re.sub(r"[^0-9a-f]", "", hkid.lower())
    data: dict[str, Any] = {
        CONF_DEVICE_ID: device_id,
        CONF_MODEL: model or info.model,
        CONF_HEATER_WATTS: 0,
        CONF_CLOUD_RUNTIME: True,
    }
    if pairing is not None:
        data[CONF_PAIRING] = dict(pairing)
    if migrate_from:
        data[CONF_MIGRATE_FROM] = migrate_from
    return ConfigSubentryData(
        data=data,
        subentry_type=SUBENTRY_THERMOSTAT,
        title=title,
        unique_id=device_id,
    )
