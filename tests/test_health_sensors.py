"""Diagnostic sensors for the device-health fields mysa-client 0.4.0 reads.

Core temperature, free memory, line voltage and the highest recorded load
current: confirmed on a Baseboard Lite (firmware 3.17.5.13). Disabled by
default, created only when the cloud state reports the value, and available
only while the structured reading that carried them is fresh.
"""

from __future__ import annotations

from datetime import timedelta
import time

from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from mysa_client.cloud import MysaConnectionError
import pytest

from custom_components.mysa_local.const import DOMAIN

from .common import DEVICE_ID, FakeCloud, hub_entry, thermostat_subentry
from .conftest import setup_entry

SENSORS = {
    "sensor.living_room_core_temperature": ("35.2", "°C", "temperature"),
    "sensor.living_room_free_memory": ("70000.0", "B", None),
    "sensor.living_room_line_voltage": ("240.0", "V", "voltage"),
    "sensor.living_room_highest_recorded_load_current": ("0.0", "A", None),
}
KEYS = dict(
    zip(
        SENSORS,
        ("core_temperature", "free_memory", "line_voltage", "max_load_current"),
        strict=True,
    )
)
UNAVAILABLE = "unavailable"


def current_clocks(cloud: FakeCloud, *, power_age: float = 0) -> None:
    """Give the structured reading a current clock (and the power section)."""
    now = time.time()
    cloud.state["latestTelemetry"]["lastConnected"] = now
    cloud.state["latestTelemetry"]["reading"]["timestamp"] = now
    cloud.state["power"]["reported"]["timestamp"] = now - power_age


def cloud_only():
    return hub_entry(subentries=[thermostat_subentry(pairing=None)])


async def setup_enabled(hass: HomeAssistant, entry) -> None:
    """Set up with the four sensors already enabled in the registry.

    Enabling them after setup would schedule Home Assistant's delayed reload.
    """
    entry.add_to_hass(hass)
    ent_reg = er.async_get(hass)
    for entity_id in SENSORS:
        ent_reg.async_get_or_create(
            "sensor",
            DOMAIN,
            f"{DEVICE_ID}_{KEYS[entity_id]}",
            suggested_object_id=entity_id.removeprefix("sensor."),
            config_entry=entry,
            disabled_by=None,
        )
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def test_created_disabled_in_the_diagnostic_category(
    hass: HomeAssistant, cloud: FakeCloud
) -> None:
    """All four exist for the Lite's fixture, disabled and diagnostic."""
    current_clocks(cloud)
    await setup_entry(hass, cloud_only())
    ent_reg = er.async_get(hass)
    for entity_id in SENSORS:
        entry = ent_reg.async_get(entity_id)
        assert entry is not None, entity_id
        assert entry.disabled_by is er.RegistryEntryDisabler.INTEGRATION
        assert entry.entity_category is EntityCategory.DIAGNOSTIC
        assert hass.states.get(entity_id) is None


async def test_values_units_and_device_classes(
    hass: HomeAssistant, cloud: FakeCloud
) -> None:
    """Once enabled, each shows the cloud value in its unit."""
    current_clocks(cloud)
    entry = cloud_only()
    await setup_enabled(hass, entry)
    for entity_id, (value, unit, device_class) in SENSORS.items():
        state = hass.states.get(entity_id)
        assert state.state == value, entity_id
        assert state.attributes["unit_of_measurement"] == unit
        assert state.attributes.get("device_class") == device_class
        assert state.attributes["state_class"] == "measurement"


@pytest.mark.parametrize(
    ("removed", "entity_id"),
    [
        ("coreTemperature", "sensor.living_room_core_temperature"),
        ("maxCurrent", "sensor.living_room_highest_recorded_load_current"),
    ],
)
async def test_not_created_without_a_value(
    hass: HomeAssistant, cloud: FakeCloud, removed: str, entity_id: str
) -> None:
    """A thermostat that doesn't report a field gets no entity for it."""
    current_clocks(cloud)
    del cloud.state["latestTelemetry"]["reading"][removed]
    await setup_entry(hass, cloud_only())
    assert er.async_get(hass).async_get(entity_id) is None


async def test_free_memory_falls_back_to_the_diagnostics_section(
    hass: HomeAssistant, cloud: FakeCloud
) -> None:
    """free_heap is reading.freeHeap, else diagnostics.reported.freeHeap."""
    current_clocks(cloud)
    del cloud.state["latestTelemetry"]["reading"]["freeHeap"]
    cloud.state["diagnostics"]["reported"]["freeHeap"] = 65000
    entry = cloud_only()
    await setup_enabled(hass, entry)
    assert hass.states.get("sensor.living_room_free_memory").state == "65000.0"


async def test_stale_reading_makes_them_unavailable(
    hass: HomeAssistant, cloud: FakeCloud
) -> None:
    """Outside the 35-minute window the values are not shown."""
    entry = cloud_only()
    current_clocks(cloud)
    cloud.auto_telemetry = False
    cloud.state["latestTelemetry"]["reading"]["timestamp"] = time.time() - 36 * 60
    await setup_enabled(hass, entry)
    for entity_id in SENSORS:
        assert hass.states.get(entity_id).state == UNAVAILABLE, entity_id


async def test_stale_power_section_hides_only_the_voltage(
    hass: HomeAssistant, cloud: FakeCloud
) -> None:
    """Voltage comes from the power section first, which can stay unchanged."""
    current_clocks(cloud, power_age=36 * 60)
    cloud.auto_telemetry = False
    entry = cloud_only()
    await setup_enabled(hass, entry)
    assert hass.states.get("sensor.living_room_line_voltage").state == UNAVAILABLE
    assert hass.states.get("sensor.living_room_core_temperature").state == "35.2"


async def test_structured_poll_failure_keeps_the_old_clock(
    hass: HomeAssistant, cloud: FakeCloud, freezer
) -> None:
    """With the structured poll failing, the carried values age out.

    The reconciled state keeps the last structured state's values, so their
    clock is that state's reading, not the time of the latest poll.
    """
    current_clocks(cloud)
    entry = cloud_only()
    await setup_enabled(hass, entry)
    thermostat = next(iter(entry.runtime_data.thermostats.values()))
    coordinator = thermostat.cloud
    assert hass.states.get("sensor.living_room_core_temperature").state == "35.2"
    freezer.tick(timedelta(minutes=40))
    real_get_states = coordinator.client.get_states

    async def structured_down(*_args, **_kwargs):
        raise MysaConnectionError("S down")

    coordinator.client.get_states = structured_down
    try:
        await coordinator.async_refresh()
        await hass.async_block_till_done()
    finally:
        coordinator.client.get_states = real_get_states
    clocks = coordinator.data.health_clocks[thermostat.device_id]
    assert dt_util.utcnow() - clocks[0] > timedelta(minutes=35)
    assert hass.states.get("sensor.living_room_core_temperature").state == UNAVAILABLE
