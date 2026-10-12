"""Metadata refresh, bounded recovery, and local capability discovery."""

import asyncio
from unittest.mock import AsyncMock, patch

from aiohomekit.exceptions import AccessoryDisconnectedError
from aiohomekit.model import Accessories, AccessoriesState
from aiohomekit.model.characteristics import CharacteristicsTypes
from aiohomekit.testing import PairingTester
from homeassistant.helpers import entity_registry as er
import pytest

from custom_components.mysa_local.protocols.homekit import (
    Field,
    HomeKitCharacteristicReadError,
    HomeKitProtocol,
    LocalMode,
)

from .common import DEVICE_ID, HKID, PAIRING_DATA, hub_entry, make_controller
from .conftest import setup_entry

CLOCK = "custom_components.mysa_local.protocols.homekit.device.monotonic"


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


@pytest.mark.parametrize("trigger", ["config", "reconnect", "backstop"])
async def test_refresh_replaces_addresses_values_limits_and_subscriptions(trigger):
    controller = await make_controller()
    pairing = controller.aliases[HKID]
    device = HomeKitProtocol.from_controller(controller).load_pairing(PAIRING_DATA)
    events = []
    device.add_listener(events.append)
    with patch(CLOCK, return_value=0) as clock:
        await device.async_connect()
        old_subscriptions = set(pairing.subscriptions)
        replace_profile(pairing, remove=(CharacteristicsTypes.TEMPERATURE_UNITS,))
        if trigger == "config":
            for listener in tuple(pairing.config_changed_listeners):
                listener(2)
        elif trigger == "reconnect":
            for listener in tuple(pairing.listeners):
                listener({})
        else:
            clock.return_value = 900
        await device.async_read((Field.TARGET_TEMPERATURE,))
        assert device.temperature_range == (7, 28, 1)
        assert device.supported_modes == (LocalMode.OFF, LocalMode.HEAT, LocalMode.COOL)
        assert Field.DISPLAY_UNIT not in device.state
        assert Field.DISPLAY_UNIT not in device.capabilities
        assert not pairing.subscriptions & old_subscriptions
        assert len(pairing.subscriptions) == 4
        assert events[-1].capabilities_changed
        assert len(pairing.listeners) == len(pairing.config_changed_listeners) == 1
        await device.async_write({Field.TARGET_TEMPERATURE: 23})
        assert (await device.async_read((Field.TARGET_TEMPERATURE,)))[
            Field.TARGET_TEMPERATURE
        ] == 23
        # Repeated connect calls do not duplicate listeners or subscriptions.
        await device.async_connect()
        assert len(pairing.listeners) == len(pairing.config_changed_listeners) == 1
        assert len(pairing.subscriptions) == 4
        await device.async_close()
        assert not pairing.listeners
        assert not pairing.config_changed_listeners


async def test_missing_address_refreshes_once_then_obeys_cooldown():
    controller = await make_controller()
    pairing = controller.aliases[HKID]
    device = HomeKitProtocol.from_controller(controller).load_pairing(PAIRING_DATA)
    with patch(CLOCK, return_value=0) as clock:
        await device.async_connect()
        replace_profile(pairing)
        original = pairing.get_characteristics

        async def read(addresses):
            known = {
                (a.aid, c.iid)
                for a in pairing.accessories
                for s in a.services
                for c in s.characteristics
            }
            if not set(addresses) <= known:
                return {address: {"status": -70409} for address in addresses}
            return await original(addresses)

        with patch.object(
            pairing, "get_characteristics", AsyncMock(side_effect=read)
        ) as reads:
            assert await device.async_read((Field.TARGET_TEMPERATURE,))
            assert reads.await_count == 2
        with (
            patch.object(
                pairing, "get_characteristics", AsyncMock(return_value={})
            ) as reads,
            patch.object(
                pairing,
                "async_populate_accessories_state",
                wraps=pairing.async_populate_accessories_state,
            ) as refresh,
        ):
            for _ in range(3):
                with pytest.raises(HomeKitCharacteristicReadError):
                    await device.async_read((Field.TARGET_TEMPERATURE,))
            assert refresh.await_count == 0
            clock.return_value = 60
            with pytest.raises(HomeKitCharacteristicReadError):
                await device.async_read((Field.TARGET_TEMPERATURE,))
            assert refresh.await_count == 1
            assert reads.await_count == 5
    await device.async_close()


async def test_subscription_failure_preserves_polling_and_removed_values():
    controller = await make_controller()
    pairing = controller.aliases[HKID]
    device = HomeKitProtocol.from_controller(controller).load_pairing(PAIRING_DATA)
    await device.async_connect()
    old_subscriptions = set(pairing.subscriptions)
    replace_profile(pairing, remove=(CharacteristicsTypes.TEMPERATURE_UNITS,))
    for listener in tuple(pairing.config_changed_listeners):
        listener(2)
    with (
        patch.object(
            pairing,
            "unsubscribe",
            AsyncMock(side_effect=AccessoryDisconnectedError("offline")),
        ),
        patch.object(
            pairing,
            "subscribe",
            AsyncMock(side_effect=AccessoryDisconnectedError("offline")),
        ),
    ):
        await device.async_read((Field.CURRENT_TEMPERATURE,))
    assert device.ready
    assert Field.DISPLAY_UNIT not in device.state
    assert not pairing.subscriptions & old_subscriptions
    await device.async_close()


async def test_concurrent_refresh_and_internal_reconnect_do_not_loop():
    controller = await make_controller()
    pairing = controller.aliases[HKID]
    device = HomeKitProtocol.from_controller(controller).load_pairing(PAIRING_DATA)
    await device.async_connect()
    for listener in tuple(pairing.config_changed_listeners):
        listener(2)

    async def populate(**_kwargs):
        for listener in tuple(pairing.listeners):
            listener({})
        await asyncio.sleep(0)

    with patch.object(
        pairing, "async_populate_accessories_state", AsyncMock(side_effect=populate)
    ) as refresh:
        await asyncio.gather(*(device.async_connect() for _ in range(4)))
    assert refresh.await_count == 1
    await device.async_close()


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


async def test_optional_removed_field_read_remaps_without_disabling_device():
    controller = await make_controller()
    pairing = controller.aliases[HKID]
    device = HomeKitProtocol.from_controller(controller).load_pairing(PAIRING_DATA)
    await device.async_connect()
    replace_profile(pairing, remove=(CharacteristicsTypes.TEMPERATURE_UNITS,))
    with patch.object(
        pairing, "get_characteristics", AsyncMock(return_value={})
    ) as read:
        assert await device.async_read((Field.DISPLAY_UNIT,)) == {}
    assert read.await_count == 1
    assert device.ready
    assert Field.DISPLAY_UNIT not in device.state
    await device.async_close()


async def test_failed_refresh_is_throttled_and_can_recover():
    controller = await make_controller()
    pairing = controller.aliases[HKID]
    device = HomeKitProtocol.from_controller(controller).load_pairing(PAIRING_DATA)
    pairing.available = False
    from custom_components.mysa_local.protocols.homekit import HomeKitConnectionError

    with (
        patch(CLOCK, return_value=1) as clock,
        patch.object(
            pairing,
            "async_populate_accessories_state",
            wraps=pairing.async_populate_accessories_state,
        ) as refresh,
    ):
        with pytest.raises(HomeKitConnectionError):
            await device.async_connect()
        for _ in range(3):
            await device.async_connect()
        assert refresh.await_count == 1
        pairing.available = True
        clock.return_value = 61
        await device.async_connect()
        assert refresh.await_count == 2
        assert device.ready
    await device.async_close()


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
