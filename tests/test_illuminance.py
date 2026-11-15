"""Illuminance from the optional HomeKit Light Sensor service (in-floor)."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import AsyncMock, patch

from aiohomekit.model import Accessories
from aiohomekit.model.characteristics import CharacteristicsTypes
from aiohomekit.testing import FakeController
from homeassistant.const import LIGHT_LUX, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from mysa_client.homekit import HomeKitProtocol
import pytest

from custom_components.mysa_local.const import DOMAIN
from custom_components.mysa_local.local_adapter import AMBIENT_LIGHT

from .common import (
    DEVICE_ID,
    HKID,
    PAIRING_DATA,
    fake_characteristic_address,
    hub_entry,
    load_json,
    thermostat_subentry,
    update_fake_characteristic,
)
from .conftest import setup_entry

ILLUMINANCE = "sensor.living_room_illuminance"
LIGHT_LEVEL = CharacteristicsTypes.LIGHT_LEVEL_CURRENT

# The Light Sensor service from the INF-V1-0 (firmware 3.17.6.5) diagnostics in
# mysa-client#29. Not checked on hardware here.
LIGHT_SENSOR_SERVICE = {
    "iid": 512,
    "type": "00000084-0000-1000-8000-0026BB765291",
    "characteristics": [
        {
            "type": "00000023-0000-1000-8000-0026BB765291",
            "iid": 514,
            "perms": ["pr"],
            "format": "string",
            "value": "Light Sensor",
            "maxLen": 64,
        },
        {
            "type": "0000006B-0000-1000-8000-0026BB765291",
            "iid": 515,
            "perms": ["pr", "ev"],
            "format": "float",
            "value": 1,
            "unit": "lux",
            "minValue": 0,
            "maxValue": 1573,
            "minStep": 1,
        },
    ],
}

needs_light_field = pytest.mark.skipif(
    AMBIENT_LIGHT is None,
    reason="installed mysa-client predates Field.AMBIENT_LIGHT (mysa-client#29)",
)


@pytest.fixture
async def light_controller() -> AsyncGenerator[FakeController]:
    """A fake controller whose paired thermostat also has a light sensor."""
    profile = load_json("homekit_bb_v2_0_l.json")
    profile[0]["services"].append(LIGHT_SENSOR_SERVICE)
    ctrl = FakeController()
    await ctrl.async_start()
    pairing = await ctrl.add_paired_device(Accessories.from_list(profile), alias=HKID)
    pairing.pairing_data = dict(PAIRING_DATA)
    ctrl.aliases[HKID] = pairing
    protocol = HomeKitProtocol.from_controller(ctrl)
    with (
        patch(
            "custom_components.mysa_local.async_get_controller",
            AsyncMock(return_value=protocol),
        ),
        patch(
            "custom_components.mysa_local.homekit_adapter.async_get_controller",
            AsyncMock(return_value=protocol),
        ),
    ):
        yield ctrl


def _local(hass: HomeAssistant) -> Any:
    runtime = hass.config_entries.async_entries(DOMAIN)[0].runtime_data
    return next(iter(runtime.thermostats.values())).local


@pytest.mark.usefixtures("controller")
async def test_no_illuminance_without_light_sensor(hass: HomeAssistant) -> None:
    """A thermostat without the Light Sensor service gets no entity."""
    await setup_entry(
        hass, hub_entry(signed_in=False, subentries=[thermostat_subentry()])
    )

    assert hass.states.get("climate.living_room") is not None
    assert hass.states.get(ILLUMINANCE) is None


@needs_light_field
async def test_illuminance_follows_homekit(
    hass: HomeAssistant, light_controller: FakeController
) -> None:
    """The entity reports lux and follows HomeKit events."""
    await setup_entry(
        hass, hub_entry(signed_in=False, subentries=[thermostat_subentry()])
    )

    state = hass.states.get(ILLUMINANCE)
    assert state is not None
    assert float(state.state) == 1.0
    assert state.attributes["unit_of_measurement"] == LIGHT_LUX
    assert state.attributes["device_class"] == "illuminance"
    assert state.attributes["state_class"] == "measurement"
    # Named from its device class: the sensor has no translation key.
    assert state.attributes["friendly_name"] == "Living Room Illuminance"
    entry = er.async_get(hass).async_get(ILLUMINANCE)
    assert entry is not None
    assert entry.unique_id == f"{DEVICE_ID}_illuminance"
    assert entry.translation_key is None

    pairing = light_controller.aliases[HKID]
    update_fake_characteristic(pairing, LIGHT_LEVEL, 412)
    await hass.async_block_till_done()
    assert float(hass.states.get(ILLUMINANCE).state) == 412.0


@needs_light_field
async def test_failed_light_read_marks_only_illuminance_unavailable(
    hass: HomeAssistant, light_controller: FakeController
) -> None:
    """A failed lux read hides the stale value; the thermostat stays local."""
    await setup_entry(
        hass, hub_entry(signed_in=False, subentries=[thermostat_subentry()])
    )
    pairing = light_controller.aliases[HKID]
    light = fake_characteristic_address(pairing, LIGHT_LEVEL)
    original = pairing.get_characteristics

    async def light_fails(addresses: list[tuple[int, int]]) -> dict:
        result = dict(await original(addresses))
        result[light] = {"status": -70402}
        return result

    with patch.object(pairing, "get_characteristics", side_effect=light_fails):
        await _local(hass).async_poll()
        await hass.async_block_till_done()

    assert hass.states.get(ILLUMINANCE).state == STATE_UNAVAILABLE
    assert hass.states.get("climate.living_room").state != STATE_UNAVAILABLE
    assert _local(hass).available

    # Recovery with the same value still brings the entity back.
    await _local(hass).async_poll()
    await hass.async_block_till_done()
    assert float(hass.states.get(ILLUMINANCE).state) == 1.0


@needs_light_field
async def test_illuminance_unavailable_while_homekit_is_down(
    hass: HomeAssistant, light_controller: FakeController
) -> None:
    """There is no cloud fallback for light level."""
    await setup_entry(
        hass, hub_entry(signed_in=False, subentries=[thermostat_subentry()])
    )

    _local(hass).mark_unavailable("test")
    await hass.async_block_till_done()

    assert hass.states.get(ILLUMINANCE).state == STATE_UNAVAILABLE
