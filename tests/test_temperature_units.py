"""Climate display steps and service conversion are independent of screen units."""

from unittest.mock import AsyncMock, patch

from aiohomekit.model.characteristics import CharacteristicsTypes
from aiohomekit.testing import FakeController
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.util.unit_system import METRIC_SYSTEM, US_CUSTOMARY_SYSTEM
import pytest

from .common import (
    DEVICE_ID,
    HKID,
    FakeCloud,
    fake_characteristic_address,
    hub_entry,
    thermostat_subentry,
    update_fake_characteristic,
)
from .conftest import setup_entry

CLIMATE = "climate.living_room"


@pytest.mark.parametrize(
    ("fahrenheit", "native_step", "expected_step"),
    [
        (False, 0.5, 0.5),
        (True, 0.5, 1.0),
        (False, 1.0, 1.0),
        (True, 1.0, 1.8),
        (True, 0.1, 0.18),
    ],
)
async def test_display_step_follows_ha_units_and_capabilities(
    hass: HomeAssistant,
    cloud: FakeCloud,
    fahrenheit: bool,
    native_step: float,
    expected_step: float,
) -> None:
    """Convert capability steps as intervals; half-Celsius uses Mysa's 1°F UI step."""
    hass.config.units = US_CUSTOMARY_SYSTEM if fahrenheit else METRIC_SYSTEM
    cloud.capabilities["features"]["climateControl"]["heat"]["setpoint"][
        "validValues"
    ] = [5, 5 + native_step, 30]
    # The thermostat screen deliberately uses the opposite unit to HA.
    screen_unit = "celsius" if fahrenheit else "fahrenheit"
    cloud.devices["DevicesObj"][DEVICE_ID]["Format"] = screen_unit
    cloud.bootstrap["data"]["devices"][DEVICE_ID]["Format"] = screen_unit
    await setup_entry(hass, hub_entry(subentries=[thermostat_subentry(pairing=None)]))
    attrs = hass.states.get(CLIMATE).attributes
    assert attrs["target_temp_step"] == expected_step
    assert attrs["min_temp"] == (41 if fahrenheit else 5)
    assert attrs["max_temp"] == (86 if fahrenheit else 30)
    assert hass.states.get("select.living_room_display_units").state == screen_unit


@pytest.mark.parametrize("local", [False, True])
async def test_fahrenheit_services_keep_celsius_commands(
    hass: HomeAssistant,
    controller: FakeController,
    cloud: FakeCloud,
    local: bool,
) -> None:
    """HA converts Fahrenheit once, validates bounds and displays confirmed readback."""
    hass.config.units = US_CUSTOMARY_SYSTEM
    pairing = controller.aliases[HKID]
    update_fake_characteristic(pairing, CharacteristicsTypes.TEMPERATURE_UNITS, 0)
    entry = (
        hub_entry()
        if local
        else hub_entry(subentries=[thermostat_subentry(pairing=None)])
    )
    await setup_entry(hass, entry)
    assert hass.states.get(CLIMATE).attributes["target_temp_step"] == 1.0
    assert hass.config.units.temperature_unit == UnitOfTemperature.FAHRENHEIT

    with patch.object(
        pairing, "put_characteristics", AsyncMock(wraps=pairing.put_characteristics)
    ) as put:
        await hass.services.async_call(
            "climate",
            "set_temperature",
            {"entity_id": CLIMATE, "temperature": 72},
            blocking=True,
        )
        expected_celsius = (72 - 32) / 1.8
        if local:
            aid, iid = fake_characteristic_address(
                pairing, CharacteristicsTypes.TEMPERATURE_TARGET
            )
            put.assert_awaited_once_with([(aid, iid, pytest.approx(expected_celsius))])
            assert cloud.updates == []
        else:
            put.assert_not_awaited()
            assert cloud.updates == [
                {
                    "source": 3,
                    "targetHeat": {"setpoint": pytest.approx(expected_celsius)},
                }
            ]
            await entry.runtime_data.cloud.async_refresh()
        await hass.async_block_till_done()
        assert hass.states.get(CLIMATE).attributes["temperature"] == 72
        write_count = put.await_count, len(cloud.updates)
        for outside_limit in (40, 87):
            with pytest.raises(ServiceValidationError):
                await hass.services.async_call(
                    "climate",
                    "set_temperature",
                    {"entity_id": CLIMATE, "temperature": outside_limit},
                    blocking=True,
                )
        assert (put.await_count, len(cloud.updates)) == write_count
