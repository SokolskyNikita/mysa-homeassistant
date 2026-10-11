"""Write routing: local first, cloud fallback, never applied twice."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import patch

from aiohomekit.testing import FakeController
from homeassistant.components.button import DOMAIN as BUTTON_DOMAIN, SERVICE_PRESS
from homeassistant.components.climate import (
    DOMAIN as CLIMATE_DOMAIN,
    SERVICE_SET_TEMPERATURE,
)
from homeassistant.components.number import DOMAIN as NUMBER_DOMAIN, SERVICE_SET_VALUE
from homeassistant.components.select import (
    DOMAIN as SELECT_DOMAIN,
    SERVICE_SELECT_OPTION,
)
from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN, SERVICE_TURN_ON
from homeassistant.const import ATTR_ENTITY_ID, ATTR_TEMPERATURE
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
import pytest

from .common import HKID, FakeCloud, hub_entry
from .conftest import setup_entry

CLIMATE = "climate.living_room"


def _thermostat(hass: HomeAssistant) -> Any:
    runtime = hass.config_entries.async_entries("mysa_local")[0].runtime_data
    return next(iter(runtime.thermostats.values()))


async def _set_temp(hass: HomeAssistant, value: float) -> None:
    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_TEMPERATURE,
        {ATTR_ENTITY_ID: CLIMATE, ATTR_TEMPERATURE: value},
        blocking=True,
    )


async def test_local_write_does_not_touch_cloud(
    hass: HomeAssistant, controller: FakeController, cloud: FakeCloud
) -> None:
    """With HomeKit up, the cloud gets no write at all."""
    await setup_entry(hass, hub_entry())
    await _set_temp(hass, 23)
    assert cloud.updates == []


async def test_timed_out_write_that_landed(
    hass: HomeAssistant, controller: FakeController, cloud: FakeCloud
) -> None:
    """A hung local write that the read-back shows as applied isn't repeated."""
    await setup_entry(hass, hub_entry())
    pairing = controller.aliases[HKID]
    real_put = pairing.put_characteristics

    async def slow_put(chars: Any) -> Any:
        await real_put(chars)  # lands on the device...
        await asyncio.sleep(3600)  # ...but the reply never comes

    with (
        patch.object(pairing, "put_characteristics", slow_put),
        patch("custom_components.mysa_local.thermostat.LOCAL_WRITE_TIMEOUT", 0.05),
    ):
        await _set_temp(hass, 23)
    assert cloud.updates == []
    assert hass.states.get(CLIMATE).attributes[ATTR_TEMPERATURE] == 23


async def test_timed_out_write_checked_in_cloud(
    hass: HomeAssistant, controller: FakeController, cloud: FakeCloud
) -> None:
    """If the read-back fails too, the cloud state decides; no double apply."""
    await setup_entry(hass, hub_entry())
    pairing = controller.aliases[HKID]

    async def hang(*args: Any) -> Any:
        await asyncio.sleep(3600)

    # Simulate the device having applied it (the cloud already reports 21.5).
    with (
        patch.object(pairing, "put_characteristics", hang),
        patch.object(pairing, "get_characteristics", hang),
        patch("custom_components.mysa_local.thermostat.LOCAL_WRITE_TIMEOUT", 0.05),
        patch("custom_components.mysa_local.local.LOCAL_READ_TIMEOUT", 0.05),
    ):
        await _set_temp(hass, 21.5)
        assert cloud.updates == []
        assert cloud.calls("GET", "/state/a1b2c3d4e5f6")

        # Not applied anywhere: the cloud write happens exactly once.
        thermostat = _thermostat(hass)
        thermostat.local.available = True
        await _set_temp(hass, 18)
    assert cloud.updates == [{"source": 3, "targetHeat": {"setpoint": 18.0}}]


async def test_rejected_local_write_is_not_retried(
    hass: HomeAssistant, controller: FakeController, cloud: FakeCloud
) -> None:
    """A device that says no isn't asked again through the cloud."""
    await setup_entry(hass, hub_entry())
    pairing = controller.aliases[HKID]

    async def refuse(chars: Any) -> Any:
        return {(1, 262): {"status": -70410, "description": "invalid value"}}

    with (
        patch.object(pairing, "put_characteristics", refuse),
        pytest.raises(HomeAssistantError, match="refused"),
    ):
        await _set_temp(hass, 22)
    assert cloud.updates == []


@pytest.mark.usefixtures("controller")
async def test_cloud_settings(hass: HomeAssistant, cloud: FakeCloud) -> None:
    """Cloud-only settings send the app's exact bodies."""
    await setup_entry(hass, hub_entry())

    await hass.services.async_call(
        SWITCH_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: "switch.living_room_button_lock"},
        blocking=True,
    )
    await hass.services.async_call(
        NUMBER_DOMAIN,
        SERVICE_SET_VALUE,
        {ATTR_ENTITY_ID: "number.living_room_sleep_brightness", "value": 30},
        blocking=True,
    )
    await hass.services.async_call(
        NUMBER_DOMAIN,
        SERVICE_SET_VALUE,
        {ATTR_ENTITY_ID: "number.living_room_highest_setpoint", "value": 29.5},
        blocking=True,
    )
    await hass.services.async_call(
        NUMBER_DOMAIN,
        SERVICE_SET_VALUE,
        {ATTR_ENTITY_ID: "number.living_room_temperature_offset", "value": -0.5},
        blocking=True,
    )
    await hass.services.async_call(
        SELECT_DOMAIN,
        SERVICE_SELECT_OPTION,
        {ATTR_ENTITY_ID: "select.living_room_heater_type", "option": "fan_forced"},
        blocking=True,
    )
    assert cloud.updates == [
        {"source": 3, "physicalInterface": {"lockout": 3}},
        {
            "source": 3,
            "physicalInterface": {"activeIntensity": 100, "idleIntensity": 30},
        },
        {"source": 3, "targetHeat": {"lockoutMax": 29.5}},
        {"source": 3, "tracking": {"ambientOffset": -0.5}},
    ]
    assert cloud.settings == [{"DutyCycleOpt": 8}]

    with pytest.raises(HomeAssistantError, match="below"):
        await hass.services.async_call(
            NUMBER_DOMAIN,
            SERVICE_SET_VALUE,
            {ATTR_ENTITY_ID: "number.living_room_lowest_setpoint", "value": 30},
            blocking=True,
        )


async def test_display_units_and_identify(
    hass: HomeAssistant, controller: FakeController, cloud: FakeCloud
) -> None:
    """Units go local while possible, cloud otherwise; identify is local only."""
    await setup_entry(hass, hub_entry())
    await hass.services.async_call(
        SELECT_DOMAIN,
        SERVICE_SELECT_OPTION,
        {ATTR_ENTITY_ID: "select.living_room_display_units", "option": "fahrenheit"},
        blocking=True,
    )
    assert hass.states.get("select.living_room_display_units").state == "fahrenheit"
    assert cloud.settings == []
    await hass.services.async_call(
        BUTTON_DOMAIN,
        SERVICE_PRESS,
        {ATTR_ENTITY_ID: "button.living_room_identify"},
        blocking=True,
    )

    controller.aliases[HKID].available = False
    await _thermostat(hass).local.async_poll()
    await hass.async_block_till_done()
    await hass.services.async_call(
        SELECT_DOMAIN,
        SERVICE_SELECT_OPTION,
        {ATTR_ENTITY_ID: "select.living_room_display_units", "option": "celsius"},
        blocking=True,
    )
    assert cloud.settings == [{"Format": "celsius"}]
    assert hass.states.get("button.living_room_identify").state == "unavailable"


async def test_cloud_backoff(
    hass: HomeAssistant, controller: FakeController, cloud: FakeCloud
) -> None:
    """Server errors stretch the poll interval; success resets it."""
    await setup_entry(hass, hub_entry())
    coordinator = _thermostat(hass).cloud
    base = coordinator.update_interval
    cloud.backend_status = 503
    await coordinator.async_refresh()
    assert coordinator.update_interval == base * 2
    await coordinator.async_refresh()
    assert coordinator.update_interval == base * 4
    cloud.backend_status = None
    await coordinator.async_refresh()
    assert coordinator.update_interval == base
    assert coordinator.last_update_success


async def test_live_mode_only_during_fallback(
    hass: HomeAssistant, controller: FakeController, cloud: FakeCloud
) -> None:
    """Live mode and fast polling run only while the LAN path is down."""
    await setup_entry(hass, hub_entry())
    thermostat = _thermostat(hass)
    coordinator = thermostat.cloud
    await coordinator.async_refresh()
    assert cloud.live_mode == []
    assert coordinator.update_interval.total_seconds() == 60

    controller.aliases[HKID].available = False
    await thermostat.local.async_poll()
    await hass.async_block_till_done()
    assert cloud.live_mode == [{"timeout": 300}]
    assert coordinator.update_interval.total_seconds() == 10
    await coordinator.async_refresh()  # renewed only every 4 minutes
    assert len(cloud.live_mode) == 1

    controller.aliases[HKID].available = True
    await thermostat.local.async_poll()
    await hass.async_block_till_done()
    assert coordinator.update_interval.total_seconds() == 60
    await coordinator.async_refresh()
    assert len(cloud.live_mode) == 1

    # A new outage starts live mode again right away.
    controller.aliases[HKID].available = False
    await thermostat.local.async_poll()
    await hass.async_block_till_done()
    assert len(cloud.live_mode) == 2
