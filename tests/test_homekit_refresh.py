"""Metadata refresh, bounded recovery, and local capability discovery."""

import asyncio
from unittest.mock import patch

from aiohomekit.model import Accessories, AccessoriesState
from aiohomekit.model.characteristics import CharacteristicsTypes
from aiohomekit.testing import PairingTester
from homeassistant.helpers import entity_registry as er
from mysa_client.homekit import Field

from .common import DEVICE_ID, HKID, hub_entry
from .conftest import setup_entry

CLOCK = "mysa_client.homekit.device.monotonic"


def replace_profile(pairing, *, remove=(), offset=100, maximum=28):
    """Replace the advertised database, including IDs, limits, and mode support."""
    profile = pairing.accessories.serialize()
    for accessory in profile:
        for service in accessory["services"]:
            service["characteristics"] = [
                c for c in service["characteristics"] if c["type"] not in remove
            ]
            for char in service["characteristics"]:
                char["iid"] += offset
                if char["type"] == CharacteristicsTypes.TEMPERATURE_TARGET:
                    char.update(minValue=7, maxValue=maximum, minStep=1)
                if char["type"] == CharacteristicsTypes.HEATING_COOLING_TARGET:
                    char["valid-values"] = [0, 1, 2]
    pairing._accessories_state = AccessoriesState(Accessories.from_list(profile), 2)
    pairing.testing = PairingTester(pairing, pairing.accessories)


async def test_local_only_offline_start_discovers_select_and_cleans_up(
    hass, controller
):
    pairing = controller.aliases[HKID]
    pairing.available = False
    entry = hub_entry(signed_in=False, cloud_enabled=False)
    with patch(CLOCK, return_value=0) as clock:
        await setup_entry(hass, entry)
        registry = er.async_get(hass)
        assert (
            registry.async_get_entity_id(
                "select", "mysa_local", f"{DEVICE_ID}_display_unit"
            )
            is None
        )
        local = next(iter(entry.runtime_data.thermostats.values())).local
        pairing.available = True
        clock.return_value = 60
        await local.async_poll()
        await hass.async_block_till_done()
        entity_id = registry.async_get_entity_id(
            "select", "mysa_local", f"{DEVICE_ID}_display_unit"
        )
        assert entity_id is not None
        assert hass.states.get(entity_id).state == "celsius"
        for _ in range(3):
            for listener in tuple(pairing.listeners):
                listener({})
            await hass.async_block_till_done(wait_background_tasks=True)
        assert (
            len(
                [
                    e
                    for e in registry.entities.values()
                    if e.unique_id == f"{DEVICE_ID}_display_unit"
                ]
            )
            == 1
        )
        await hass.config_entries.async_unload(entry.entry_id)
        assert not pairing.listeners
        assert not pairing.config_changed_listeners
        assert not local._LocalThermostat__capability_listeners


async def test_config_change_removes_essential_field_and_updates_ha_limits(
    hass, controller
):
    entry = hub_entry(signed_in=False, cloud_enabled=False)
    await setup_entry(hass, entry)
    pairing = controller.aliases[HKID]
    local = next(iter(entry.runtime_data.thermostats.values())).local
    replace_profile(pairing)
    for listener in tuple(pairing.config_changed_listeners):
        listener(2)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert hass.states.get("climate.living_room").attributes["max_temp"] == 28
    replace_profile(pairing, remove=(CharacteristicsTypes.TEMPERATURE_TARGET,))
    for listener in tuple(pairing.config_changed_listeners):
        listener(3)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert not local.available
    assert Field.TARGET_TEMPERATURE not in local.values
    assert hass.states.get("climate.living_room").state == "unavailable"


async def test_unload_cancels_pending_metadata_refresh(hass, controller):
    entry = hub_entry(signed_in=False, cloud_enabled=False)
    await setup_entry(hass, entry)
    pairing = controller.aliases[HKID]
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def populate(**_kwargs):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    with patch.object(pairing, "async_populate_accessories_state", populate):
        for listener in tuple(pairing.config_changed_listeners):
            listener(2)
        await started.wait()
        await hass.config_entries.async_unload(entry.entry_id)
    assert cancelled.is_set()
    assert not pairing.listeners
    assert not pairing.config_changed_listeners


async def test_offline_start_keeps_cause_and_recovers_with_saved_pairing(
    hass, controller
):
    """Polling an undiscovered device retains the cause and later reconnects."""
    pairing = controller.aliases[HKID]
    pairing.available = False
    entry = hub_entry(signed_in=False, cloud_enabled=False)
    with patch(CLOCK, return_value=1) as clock:
        await setup_entry(hass, entry)
        local = next(iter(entry.runtime_data.thermostats.values())).local
        original_pairing = local.pairing_data
        original_error = local.last_error
        assert original_error == "HomeKit accessory was not found"
        clock.return_value = 31
        await local.async_poll()
        assert not local.available
        assert local.last_error == original_error
        assert local.diagnostics()["last_error"] == original_error
        assert local.diagnostics()["down_since"] is not None
        pairing.available = True
        clock.return_value = 61
        await local.async_poll()
        await hass.async_block_till_done()
        assert local.available
        assert local.last_error is None
        assert local.diagnostics()["down_since"] is None
        assert local.pairing_data == original_pairing
        assert hass.states.get("climate.living_room").state == "heat"
    await hass.config_entries.async_unload(entry.entry_id)


async def test_established_connection_recovers_without_pairing_again(hass, controller):
    """A transient outage reuses saved keys and restores the existing entity."""
    entry = hub_entry(signed_in=False, cloud_enabled=False)
    await setup_entry(hass, entry)
    local = next(iter(entry.runtime_data.thermostats.values())).local
    pairing = controller.aliases[HKID]
    original_pairing = local.pairing_data
    assert local.available
    pairing.available = False
    await local.async_poll()
    await hass.async_block_till_done()
    assert not local.available
    assert hass.states.get("climate.living_room").state == "unavailable"
    pairing.available = True
    await local.async_poll()
    await hass.async_block_till_done()
    assert local.available
    assert local.pairing_data == original_pairing
    assert hass.states.get("climate.living_room").state == "heat"
    await hass.config_entries.async_unload(entry.entry_id)
