"""Setpoint limits may come from reported state or the device record."""

from __future__ import annotations

from typing import Any

from homeassistant.components.climate import (
    ATTR_MAX_TEMP,
    ATTR_MIN_TEMP,
    DOMAIN as CLIMATE_DOMAIN,
    SERVICE_SET_TEMPERATURE,
)
from homeassistant.components.number import DOMAIN as NUMBER_DOMAIN, SERVICE_SET_VALUE
from homeassistant.const import ATTR_ENTITY_ID, ATTR_TEMPERATURE
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from mysa_client.cloud import DeviceRecord
import pytest

from custom_components.mysa_local.const import DOMAIN

from .common import DEVICE_ID, FakeCloud, hub_entry, thermostat_subentry
from .conftest import setup_entry


def _thermostat(hass: HomeAssistant) -> Any:
    """Return the configured thermostat."""
    runtime = hass.config_entries.async_entries(DOMAIN)[0].runtime_data
    return next(iter(runtime.thermostats.values()))


def test_device_record_setpoint_limits_are_numeric() -> None:
    """Only numeric record limits are accepted; bools and strings are malformed."""
    record = DeviceRecord.from_api(
        DEVICE_ID,
        {"MinSetpoint": 5, "MaxSetpoint": 30},
    )
    assert (record.setpoint_min, record.setpoint_max) == (5.0, 30.0)

    malformed = DeviceRecord.from_api(
        DEVICE_ID,
        {"MinSetpoint": "5", "MaxSetpoint": True},
    )
    assert malformed.setpoint_min is None
    assert malformed.setpoint_max is None


@pytest.mark.usefixtures("controller")
async def test_reported_setpoint_limits_precede_record_and_desired(
    hass: HomeAssistant, cloud: FakeCloud
) -> None:
    """Reported limits win; desired-only limits are never treated as confirmed."""
    record = cloud.devices["DevicesObj"][DEVICE_ID]
    record.update({"MinSetpoint": 9, "MaxSetpoint": 27})
    target_heat = cloud.state["targetHeat"]
    target_heat["desired"].update({"lockoutMin": 1, "lockoutMax": 35})
    target_heat["reported"].update({"lockoutMin": 7, "lockoutMax": 29})

    await setup_entry(hass, hub_entry())

    thermostat = _thermostat(hass)
    assert (thermostat.setpoint_min, thermostat.setpoint_max) == (7, 29)
    assert thermostat.setpoint_range == (7, 29, 0.5)
    assert hass.states.get("number.living_room_lowest_setpoint").state == "7.0"
    assert hass.states.get("number.living_room_highest_setpoint").state == "29.0"


@pytest.mark.usefixtures("controller")
async def test_record_limits_fill_missing_reported_values_and_validate_writes(
    hass: HomeAssistant, cloud: FakeCloud
) -> None:
    """Record limits fill missing state, constrain climate, and reject crossed bounds."""
    record = cloud.devices["DevicesObj"][DEVICE_ID]
    record.update({"MinSetpoint": 8, "MaxSetpoint": 28})
    target_heat = cloud.state["targetHeat"]
    target_heat["reported"].pop("lockoutMin")
    target_heat["reported"].pop("lockoutMax")
    # Desired values are not confirmed and must not override the settings record.
    target_heat["desired"].update({"lockoutMin": 1, "lockoutMax": 35})

    await setup_entry(hass, hub_entry())

    thermostat = _thermostat(hass)
    assert (thermostat.setpoint_min, thermostat.setpoint_max) == (8, 28)
    assert thermostat.setpoint_range == (8, 28, 0.5)
    climate = hass.states.get("climate.living_room")
    assert climate.attributes[ATTR_MIN_TEMP] == 8
    assert climate.attributes[ATTR_MAX_TEMP] == 28
    assert hass.states.get("number.living_room_lowest_setpoint").state == "8.0"
    assert hass.states.get("number.living_room_highest_setpoint").state == "28.0"

    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            CLIMATE_DOMAIN,
            SERVICE_SET_TEMPERATURE,
            {ATTR_ENTITY_ID: "climate.living_room", ATTR_TEMPERATURE: 7},
            blocking=True,
        )
    for entity_id, value in (
        ("number.living_room_lowest_setpoint", 28),
        ("number.living_room_highest_setpoint", 8),
    ):
        with pytest.raises(HomeAssistantError):
            await hass.services.async_call(
                NUMBER_DOMAIN,
                SERVICE_SET_VALUE,
                {ATTR_ENTITY_ID: entity_id, "value": value},
                blocking=True,
            )
    assert cloud.updates == []


@pytest.mark.usefixtures("controller")
async def test_missing_or_malformed_record_limits_remain_unknown(
    hass: HomeAssistant, cloud: FakeCloud
) -> None:
    """Missing and malformed record values do not become bogus number states."""
    record = cloud.devices["DevicesObj"][DEVICE_ID]
    record["MinSetpoint"] = "5"
    record["MaxSetpoint"] = True
    target_heat = cloud.state["targetHeat"]
    target_heat["reported"].pop("lockoutMin")
    target_heat["reported"].pop("lockoutMax")

    await setup_entry(hass, hub_entry())

    thermostat = _thermostat(hass)
    assert thermostat.setpoint_min is None
    assert thermostat.setpoint_max is None
    assert hass.states.get("number.living_room_lowest_setpoint").state == "unknown"
    assert hass.states.get("number.living_room_highest_setpoint").state == "unknown"


async def test_infloor_can_set_reported_40_degree_limit(
    hass: HomeAssistant, cloud: FakeCloud
) -> None:
    """In-Floor capability data can raise the ceiling above the 30°C fallback."""
    for record in (
        cloud.devices["DevicesObj"][DEVICE_ID],
        cloud.bootstrap["data"]["devices"][DEVICE_ID],
    ):
        record.update({"Model": "INF-V1-0", "MaxSetpoint": 40})
    cloud.state["identity"]["reported"]["model"] = "INF-V1-0"
    cloud.state["targetHeat"]["reported"]["lockoutMax"] = 40
    cloud.capabilities["system"]["model"] = "INF-V1-0"
    cloud.capabilities["features"]["climateControl"]["heat"]["setpoint"][
        "validValues"
    ] = [5 + i / 2 for i in range(71)]
    entry = hub_entry(subentries=[thermostat_subentry(pairing=None)])
    await setup_entry(hass, entry)
    climate_id = "climate.living_room"
    assert hass.states.get(climate_id).attributes[ATTR_MAX_TEMP] == 40

    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_TEMPERATURE,
        {ATTR_ENTITY_ID: climate_id, ATTR_TEMPERATURE: 40},
        blocking=True,
    )
    assert cloud.updates == [{"source": 3, "targetHeat": {"setpoint": 40}}]
    await entry.runtime_data.cloud.async_refresh()
    assert hass.states.get(climate_id).attributes[ATTR_TEMPERATURE] == 40

    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            CLIMATE_DOMAIN,
            SERVICE_SET_TEMPERATURE,
            {ATTR_ENTITY_ID: climate_id, ATTR_TEMPERATURE: 40.5},
            blocking=True,
        )
    assert len(cloud.updates) == 1
