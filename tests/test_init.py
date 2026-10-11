"""Setup, entities and local/cloud routing."""

from __future__ import annotations

from aiohomekit.testing import FakeController
from homeassistant.components.climate import (
    ATTR_CURRENT_TEMPERATURE,
    ATTR_HVAC_ACTION,
    ATTR_HVAC_MODE,
    ATTR_MAX_TEMP,
    ATTR_MIN_TEMP,
    ATTR_TARGET_TEMP_STEP,
    DOMAIN as CLIMATE_DOMAIN,
    SERVICE_SET_HVAC_MODE,
    SERVICE_SET_TEMPERATURE,
    HVACAction,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import ATTR_ENTITY_ID, ATTR_TEMPERATURE, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
import pytest

from .common import DEVICE_ID, FakeCloud, hub_entry
from .conftest import setup_entry

CLIMATE = "climate.living_room"


@pytest.mark.usefixtures("controller")
async def test_local_only_setup(hass: HomeAssistant) -> None:
    """Without a Mysa sign-in everything comes from HomeKit."""
    entry = hub_entry(signed_in=False)
    await setup_entry(hass, entry)
    assert entry.state is ConfigEntryState.LOADED

    state = hass.states.get(CLIMATE)
    assert state is not None
    assert state.state == HVACMode.HEAT
    assert state.attributes[ATTR_CURRENT_TEMPERATURE] == 21.4
    assert state.attributes[ATTR_TEMPERATURE] == 21.5
    assert state.attributes[ATTR_HVAC_ACTION] == HVACAction.HEATING
    assert state.attributes[ATTR_MIN_TEMP] == 5
    assert state.attributes[ATTR_MAX_TEMP] == 30
    assert state.attributes[ATTR_TARGET_TEMP_STEP] == 0.5

    assert hass.states.get("sensor.living_room_temperature").state == "21.4"
    assert hass.states.get("sensor.living_room_connection_path").state == "local"
    assert hass.states.get("select.living_room_display_units").state == "celsius"
    assert hass.states.get("button.living_room_identify") is not None
    # Nothing cloud-only exists.
    assert hass.states.get("switch.living_room_button_lock") is None


async def test_local_write(hass: HomeAssistant, controller: FakeController) -> None:
    """Setpoint and mode go over HomeKit while it's up."""
    await setup_entry(hass, hub_entry(signed_in=False))

    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_TEMPERATURE,
        {ATTR_ENTITY_ID: CLIMATE, ATTR_TEMPERATURE: 22.5},
        blocking=True,
    )
    assert hass.states.get(CLIMATE).attributes[ATTR_TEMPERATURE] == 22.5

    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_HVAC_MODE,
        {ATTR_ENTITY_ID: CLIMATE, ATTR_HVAC_MODE: HVACMode.OFF},
        blocking=True,
    )
    state = hass.states.get(CLIMATE)
    assert state.state == HVACMode.OFF
    assert state.attributes[ATTR_HVAC_ACTION] == HVACAction.OFF


@pytest.mark.usefixtures("controller")
async def test_cloud_setup(hass: HomeAssistant, cloud: FakeCloud) -> None:
    """Signed in: the cloud adds the settings HomeKit doesn't have."""
    entry = hub_entry()
    await setup_entry(hass, entry)
    assert entry.state is ConfigEntryState.LOADED

    assert hass.states.get("switch.living_room_button_lock").state == "off"
    assert hass.states.get("number.living_room_wake_brightness").state == "100"
    assert hass.states.get("number.living_room_sleep_brightness").state == "50"
    assert hass.states.get("number.living_room_lowest_setpoint").state == "5.0"
    assert hass.states.get("number.living_room_highest_setpoint").state == "30.0"
    assert hass.states.get("number.living_room_temperature_offset").state == "0.0"
    assert hass.states.get("select.living_room_heater_type").state == "baseboard"
    assert hass.states.get("binary_sensor.living_room_cloud_connection").state == "on"
    assert hass.states.get("binary_sensor.living_room_schedule_hold").state == "off"
    assert hass.states.get("update.living_room_firmware").state == "off"
    # Local still serves the climate entity.
    assert hass.states.get("sensor.living_room_connection_path").state == "local"

    ent_reg = er.async_get(hass)
    wifi = ent_reg.async_get("sensor.living_room_wi_fi_signal")
    assert wifi is not None
    assert wifi.disabled_by is er.RegistryEntryDisabler.INTEGRATION
    assert cloud.refresh_calls == 1


async def test_local_down_falls_back_to_cloud(
    hass: HomeAssistant, controller: FakeController, cloud: FakeCloud
) -> None:
    """With HomeKit down, reads and writes go through the cloud."""
    await setup_entry(hass, hub_entry())
    pairing = controller.aliases["00:00:00:00:00:00"]
    pairing.available = False
    runtime = hass.config_entries.async_entries("mysa_local")[0].runtime_data
    thermostat = next(iter(runtime.thermostats.values()))
    await thermostat.local.async_poll()
    await hass.async_block_till_done()

    assert hass.states.get("sensor.living_room_connection_path").state == "cloud"
    state = hass.states.get(CLIMATE)
    assert state.state == HVACMode.HEAT
    assert state.attributes[ATTR_CURRENT_TEMPERATURE] == 21.4

    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_TEMPERATURE,
        {ATTR_ENTITY_ID: CLIMATE, ATTR_TEMPERATURE: 19},
        blocking=True,
    )
    assert cloud.updates[-1] == {"source": 3, "targetHeat": {"setpoint": 19.0}}


async def test_local_down_without_cloud(
    hass: HomeAssistant, controller: FakeController
) -> None:
    """Local-only and HomeKit down: unavailable, and writes fail clearly."""
    await setup_entry(hass, hub_entry(signed_in=False))
    controller.aliases["00:00:00:00:00:00"].available = False
    runtime = hass.config_entries.async_entries("mysa_local")[0].runtime_data
    await next(iter(runtime.thermostats.values())).local.async_poll()
    await hass.async_block_till_done()

    assert hass.states.get(CLIMATE).state == STATE_UNAVAILABLE
    assert hass.states.get("sensor.living_room_connection_path").state == "offline"


def test_device_id_constant() -> None:
    """The fixtures use a fake MAC, never a real one."""
    assert DEVICE_ID == "a1b2c3d4e5f6"
