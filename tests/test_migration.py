"""HomeKit Device takeover must retain recovery state until completion."""

from unittest.mock import AsyncMock, patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers import device_registry as dr, entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mysa_local.const import CONF_MIGRATE_FROM, DOMAIN
from custom_components.mysa_local.migration import async_migrate_homekit_controller

from .common import DEVICE_ID, hub_entry, thermostat_subentry


@pytest.fixture(autouse=True)
def mock_accessory_unpair():
    """The source integration's device-side unpair is outside registry takeover."""
    with patch.object(MockConfigEntry, "async_remove", AsyncMock()):
        yield


def migration_entries(hass):
    source = MockConfigEntry(
        domain="homekit_controller", title="Old thermostat", data={}
    )
    source.add_to_hass(hass)
    data = thermostat_subentry()
    data["data"][CONF_MIGRATE_FROM] = source.entry_id
    destination = hub_entry(subentries=[data])
    destination.add_to_hass(hass)
    subentry = next(iter(destination.subentries.values()))
    return source, destination, subentry


async def test_failed_unload_retains_marker_then_retry_moves_entities(hass):
    source, destination, subentry = migration_entries(hass)
    source.mock_state(hass, ConfigEntryState.LOADED)
    registry = er.async_get(hass)
    old = registry.async_get_or_create(
        "climate",
        "homekit_controller",
        "old-climate",
        config_entry=source,
        suggested_object_id="favorite_heater",
    )
    registry.async_update_entity(old.entity_id, name="My heater", icon="mdi:fire")
    devices = dr.async_get(hass)
    old_device = devices.async_get_or_create(
        config_entry_id=source.entry_id,
        identifiers={("homekit_controller", "old-device")},
    )
    devices.async_update_device(
        old_device.id, name_by_user="Custom room", labels={"old-label"}
    )

    with patch.object(
        hass.config_entries, "async_unload", AsyncMock(return_value=False)
    ):
        await async_migrate_homekit_controller(hass, destination, subentry)
    assert (
        destination.subentries[subentry.subentry_id].data[CONF_MIGRATE_FROM]
        == source.entry_id
    )
    assert registry.async_get(old.entity_id).platform == "homekit_controller"

    async def unload(_entry_id, **_kwargs):
        source.mock_state(hass, ConfigEntryState.NOT_LOADED)
        return True

    with patch.object(
        hass.config_entries, "async_unload", AsyncMock(side_effect=unload)
    ):
        await async_migrate_homekit_controller(
            hass, destination, destination.subentries[subentry.subentry_id]
        )
    assert CONF_MIGRATE_FROM not in destination.subentries[subentry.subentry_id].data
    assert hass.config_entries.async_get_entry(source.entry_id) is None
    moved = registry.async_get(old.entity_id)
    assert moved.platform == DOMAIN
    assert moved.unique_id == f"{DEVICE_ID}_climate"
    assert moved.name == "My heater"
    assert moved.icon == "mdi:fire"
    device = devices.async_get(moved.device_id)
    assert device.name_by_user == "Custom room"
    assert device.labels == {"old-label"}


async def test_partial_takeover_retry_preserves_existing_destination(hass):
    source, destination, subentry = migration_entries(hass)
    registry = er.async_get(hass)
    existing = registry.async_get_or_create(
        "climate",
        DOMAIN,
        f"{DEVICE_ID}_climate",
        config_entry=destination,
        config_subentry_id=subentry.subentry_id,
    )
    registry.async_get_or_create(
        "climate", "homekit_controller", "old-climate", config_entry=source
    )
    select = registry.async_get_or_create(
        "select", "homekit_controller", "old-units", config_entry=source
    )
    devices = dr.async_get(hass)
    old_device = devices.async_get_or_create(
        config_entry_id=source.entry_id,
        identifiers={("homekit_controller", "old-device")},
    )
    devices.async_update_device(
        old_device.id, name_by_user="Old name", labels={"old-label"}
    )
    current = devices.async_get_or_create(
        config_entry_id=destination.entry_id,
        config_subentry_id=subentry.subentry_id,
        identifiers={(DOMAIN, DEVICE_ID)},
    )
    devices.async_update_device(
        current.id, name_by_user="Keep this", labels={"new-label"}
    )
    with (
        patch.object(
            hass.config_entries,
            "async_remove",
            AsyncMock(side_effect=RuntimeError("interrupted")),
        ),
        pytest.raises(RuntimeError, match="interrupted"),
    ):
        await async_migrate_homekit_controller(hass, destination, subentry)
    assert CONF_MIGRATE_FROM in destination.subentries[subentry.subentry_id].data
    await async_migrate_homekit_controller(
        hass, destination, destination.subentries[subentry.subentry_id]
    )
    assert CONF_MIGRATE_FROM not in destination.subentries[subentry.subentry_id].data
    assert registry.async_get(existing.entity_id).unique_id == f"{DEVICE_ID}_climate"
    assert registry.async_get(select.entity_id).unique_id == f"{DEVICE_ID}_display_unit"
    current = devices.async_get(current.id)
    assert current.name_by_user == "Keep this"
    assert current.labels == {"old-label", "new-label"}


async def test_already_absent_source_clears_marker(hass):
    source, destination, subentry = migration_entries(hass)
    await hass.config_entries.async_remove(source.entry_id)
    await async_migrate_homekit_controller(hass, destination, subentry)
    assert CONF_MIGRATE_FROM not in destination.subentries[subentry.subentry_id].data
