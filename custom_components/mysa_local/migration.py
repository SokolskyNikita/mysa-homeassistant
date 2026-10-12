"""Moving a thermostat over from the HomeKit Device integration.

The config flow has already added our own HomeKit pairing. Here we move the old
entities (keeping their entity IDs, names and areas) and remove the HomeKit
Device entry, which also removes its pairing from the thermostat.
"""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry, ConfigEntryState, ConfigSubentry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er

from .const import CONF_DEVICE_ID, CONF_MIGRATE_FROM, DOMAIN, LOGGER, MANUFACTURER

HKC_DOMAIN = "homekit_controller"


def _our_key(entry: er.RegistryEntry) -> str | None:
    """Which of our entities replaces a HomeKit Device entity, if any."""
    if entry.domain == Platform.CLIMATE:
        return "climate"
    if entry.domain == Platform.BUTTON:
        return "identify"
    if entry.domain == Platform.SELECT:
        return "display_unit"
    if entry.domain == Platform.SENSOR and (
        entry.original_device_class == "temperature"
        or entry.device_class == "temperature"
    ):
        return "temperature"
    return None


def mac_from_device_id(device_id: str) -> str:
    """Aabbccddeeff -> aa:bb:cc:dd:ee:ff."""
    return ":".join(device_id[i : i + 2] for i in range(0, 12, 2))


async def async_migrate_homekit_controller(
    hass: HomeAssistant, entry: ConfigEntry, subentry: ConfigSubentry
) -> None:
    """Take over the HomeKit Device entry named in the subentry, then forget it."""
    source_id = subentry.data.get(CONF_MIGRATE_FROM)
    source = hass.config_entries.async_get_entry(source_id) if source_id else None
    if source is not None:
        if source.domain != HKC_DOMAIN:
            return
        if not await _async_move(hass, entry, subentry, source):
            return
    hass.config_entries.async_update_subentry(
        entry,
        subentry,
        data={k: v for k, v in subentry.data.items() if k != CONF_MIGRATE_FROM},
    )


async def _async_move(
    hass: HomeAssistant,
    entry: ConfigEntry,
    subentry: ConfigSubentry,
    source: ConfigEntry,
) -> bool:
    if source.state in (
        ConfigEntryState.LOADED,
        ConfigEntryState.FAILED_UNLOAD,
    ) and not await hass.config_entries.async_unload(source.entry_id):
        LOGGER.warning(
            "Couldn't unload HomeKit Device entry %s; migration will retry on setup",
            source.title,
        )
        return False

    ent_reg = er.async_get(hass)
    dev_reg = dr.async_get(hass)
    device_id: str = subentry.data[CONF_DEVICE_ID]

    old_devices = dr.async_entries_for_config_entry(dev_reg, source.entry_id)
    new_device = dev_reg.async_get_or_create(
        config_entry_id=entry.entry_id,
        config_subentry_id=subentry.subentry_id,
        identifiers={(DOMAIN, device_id)},
        connections={(dr.CONNECTION_NETWORK_MAC, mac_from_device_id(device_id))},
        manufacturer=MANUFACTURER,
        name=subentry.title,
    )
    if old_devices:
        old = old_devices[0]
        dev_reg.async_update_device(
            new_device.id,
            area_id=new_device.area_id or old.area_id,
            name_by_user=new_device.name_by_user or old.name_by_user,
            labels=set(new_device.labels) | set(old.labels),
        )

    moved = []
    for old_entity in er.async_entries_for_config_entry(ent_reg, source.entry_id):
        key = _our_key(old_entity)
        if key is None:
            continue
        unique_id = f"{device_id}_{key}"
        if ent_reg.async_get_entity_id(old_entity.domain, DOMAIN, unique_id):
            continue
        ent_reg.async_update_entity_platform(
            old_entity.entity_id,
            DOMAIN,
            new_config_entry_id=entry.entry_id,
            new_config_subentry_id=subentry.subentry_id,
            new_unique_id=unique_id,
            new_device_id=new_device.id,
        )
        moved.append(old_entity.entity_id)

    LOGGER.info(
        "Moved %s from HomeKit Device to Mysa Local: %s",
        source.title,
        ", ".join(moved) or "no entities",
    )
    await hass.config_entries.async_remove(source.entry_id)
    return hass.config_entries.async_get_entry(source.entry_id) is None
