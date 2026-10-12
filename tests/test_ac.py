"""Mini-Split AC fan speed and swing (cloud only).

Values follow Mysa's app 4.20.1: the settings record's SupportedModes lists
what the remote can send, and writes go to the `modes` state section.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.climate import (
    ATTR_FAN_MODE,
    ATTR_FAN_MODES,
    ATTR_HVAC_MODE,
    ATTR_SWING_HORIZONTAL_MODE,
    ATTR_SWING_HORIZONTAL_MODES,
    ATTR_SWING_MODE,
    ATTR_SWING_MODES,
    DOMAIN as CLIMATE_DOMAIN,
    SERVICE_SET_FAN_MODE,
    SERVICE_SET_HVAC_MODE,
    SERVICE_SET_SWING_HORIZONTAL_MODE,
    SERVICE_SET_SWING_MODE,
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.const import ATTR_ENTITY_ID, ATTR_SUPPORTED_FEATURES
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
import pytest

from .common import DEVICE_ID, FakeCloud, hub_entry, thermostat_subentry
from .conftest import setup_entry

CLIMATE = "climate.living_room"
FAN_AND_SWING = (
    ClimateEntityFeature.FAN_MODE
    | ClimateEntityFeature.SWING_MODE
    | ClimateEntityFeature.SWING_HORIZONTAL_MODE
)

# What the remote offers in heat mode ("3"): an older remote with
# auto/low/medium/high, vertical off/on/auto and two positions (11 = toggle is
# skipped), horizontal off/auto (12 = not available is skipped).
HEAT_MODE = {
    "temperatures": [16, 17, 18, 30],
    "fanSpeeds": [1, 2, 4, 6],
    "verticalSwing": [1, 2, 3, 4, 5, 11],
    "horizontalSwing": [1, 3, 12],
}


def _make_ac(cloud: FakeCloud, **record: Any) -> None:
    """Turn the test thermostat's cloud record into a Mini-Split AC."""
    fields = {"Model": "AC-V1-0", "SupportedModes": {"3": HEAT_MODE}, **record}
    cloud.devices["DevicesObj"][DEVICE_ID].update(fields)
    cloud.bootstrap["data"]["devices"][DEVICE_ID].update(fields)
    cloud.state["modes"]["reported"].update(
        {"fan_mode": 2, "verticalSwingState": 3, "horizontalSwingState": 1}
    )


async def _call(hass: HomeAssistant, service: str, data: dict[str, Any]) -> None:
    await hass.services.async_call(
        CLIMATE_DOMAIN, service, {ATTR_ENTITY_ID: CLIMATE, **data}, blocking=True
    )


@pytest.mark.usefixtures("controller")
async def test_ac_fan_and_swing(hass: HomeAssistant, cloud: FakeCloud) -> None:
    """The remote's options and the cloud's current values show on the climate."""
    _make_ac(cloud)
    await setup_entry(hass, hub_entry())
    attrs = hass.states.get(CLIMATE).attributes
    assert attrs[ATTR_SUPPORTED_FEATURES] & FAN_AND_SWING == FAN_AND_SWING
    assert attrs[ATTR_FAN_MODES] == ["auto", "low", "medium", "high"]
    assert attrs[ATTR_FAN_MODE] == "medium"  # stored 2
    assert attrs[ATTR_SWING_MODES] == [
        "off",
        "on",
        "auto",
        "position_1",
        "position_2",
    ]
    assert attrs[ATTR_SWING_MODE] == "auto"
    assert attrs[ATTR_SWING_HORIZONTAL_MODES] == ["off", "auto"]
    assert attrs[ATTR_SWING_HORIZONTAL_MODE] == "off"


@pytest.mark.usefixtures("controller")
async def test_ac_writes(hass: HomeAssistant, cloud: FakeCloud) -> None:
    """Fan (with the current mode, as Mysa's app does) and swing go to `modes`."""
    _make_ac(cloud)
    entry = hub_entry()
    await setup_entry(hass, entry)

    await _call(hass, SERVICE_SET_FAN_MODE, {ATTR_FAN_MODE: "high"})
    assert cloud.updates[-1] == {"source": 3, "modes": {"fan_mode": 3, "mode": 4}}
    await _call(hass, SERVICE_SET_SWING_MODE, {ATTR_SWING_MODE: "position_1"})
    assert cloud.updates[-1] == {"source": 3, "modes": {"verticalSwingState": 4}}
    await _call(
        hass, SERVICE_SET_SWING_HORIZONTAL_MODE, {ATTR_SWING_HORIZONTAL_MODE: "auto"}
    )
    assert cloud.updates[-1] == {"source": 3, "modes": {"horizontalSwingState": 3}}

    await entry.runtime_data.cloud.async_refresh()
    await hass.async_block_till_done()
    attrs = hass.states.get(CLIMATE).attributes
    assert attrs[ATTR_FAN_MODE] == "high"
    assert attrs[ATTR_SWING_MODE] == "position_1"
    assert attrs[ATTR_SWING_HORIZONTAL_MODE] == "auto"

    # Options the remote doesn't have are refused before anything is sent.
    with pytest.raises(ServiceValidationError):
        await _call(hass, SERVICE_SET_FAN_MODE, {ATTR_FAN_MODE: "speed_4"})
    assert len(cloud.updates) == 3


@pytest.mark.usefixtures("controller")
async def test_ac_newer_remote(hass: HomeAssistant, cloud: FakeCloud) -> None:
    """Newer remotes number their speeds; those are stored as 4-10."""
    _make_ac(
        cloud,
        SupportedCapsVersion="1.2",
        SupportedModes={"3": {"fanSpeeds": [1, 3, 5, 7, 8, 12]}},
    )
    cloud.state["modes"]["reported"]["fan_mode"] = 11
    await setup_entry(hass, hub_entry())
    attrs = hass.states.get(CLIMATE).attributes
    assert attrs[ATTR_FAN_MODES] == [
        "auto",
        "speed_1",
        "speed_2",
        "speed_3",
        "speed_4",
        "sleep",
    ]
    assert attrs[ATTR_FAN_MODE] == "sleep"
    assert attrs[ATTR_SUPPORTED_FEATURES] & ClimateEntityFeature.SWING_MODE == 0

    await _call(hass, SERVICE_SET_FAN_MODE, {ATTR_FAN_MODE: "speed_2"})
    assert cloud.updates[-1]["modes"] == {"fan_mode": 5, "mode": 4}


@pytest.mark.usefixtures("controller")
async def test_ac_customized_remote(hass: HomeAssistant, cloud: FakeCloud) -> None:
    """With modifiedKeys, only options whose IR key was kept are offered."""
    # 8 = fan auto, 9 = fan low, 39 = vertical swing on; no medium/high key and
    # no horizontal swing key.
    _make_ac(cloud, modifiedKeys=[8, 9, 39])
    await setup_entry(hass, hub_entry())
    attrs = hass.states.get(CLIMATE).attributes
    assert attrs[ATTR_FAN_MODES] == ["auto", "low"]
    assert attrs[ATTR_SWING_MODES] == [
        "off",
        "on",
        "auto",
        "position_1",
        "position_2",
    ]
    assert (
        attrs[ATTR_SUPPORTED_FEATURES] & ClimateEntityFeature.SWING_HORIZONTAL_MODE == 0
    )


@pytest.mark.usefixtures("controller")
async def test_ac_off_hides_fan_and_swing(
    hass: HomeAssistant, cloud: FakeCloud
) -> None:
    """While off, fan and swing are hidden: a change would switch the AC on."""
    _make_ac(cloud)
    await setup_entry(hass, hub_entry())
    await _call(hass, SERVICE_SET_HVAC_MODE, {ATTR_HVAC_MODE: HVACMode.OFF})
    attrs = hass.states.get(CLIMATE).attributes
    assert attrs[ATTR_SUPPORTED_FEATURES] & FAN_AND_SWING == 0
    assert ATTR_FAN_MODES not in attrs


@pytest.mark.usefixtures("controller")
async def test_heaters_have_no_fan_or_swing(
    hass: HomeAssistant, cloud: FakeCloud
) -> None:
    """Other models never get them, even with a fan_mode in their state."""
    cloud.state["modes"]["reported"]["fan_mode"] = 9
    await setup_entry(hass, hub_entry())
    attrs = hass.states.get(CLIMATE).attributes
    assert attrs[ATTR_SUPPORTED_FEATURES] & FAN_AND_SWING == 0
    assert ATTR_FAN_MODE not in attrs


async def test_ac_starts_off_despite_desired_cooling(
    hass: HomeAssistant, cloud: FakeCloud
) -> None:
    """Startup trusts reported OFF and sends no command to synchronize the AC."""
    _make_ac(cloud)
    cloud.state["modes"]["reported"]["mode"] = 0
    cloud.state["modes"]["desired"]["mode"] = 3
    entry = hub_entry(subentries=[thermostat_subentry(pairing=None)])
    await setup_entry(hass, entry)

    state = hass.states.get(CLIMATE)
    assert state.state == HVACMode.OFF
    assert state.attributes["hvac_action"] == "off"
    assert state.attributes[ATTR_SUPPORTED_FEATURES] & FAN_AND_SWING == 0
    assert cloud.updates == []
    assert cloud.settings == []


@pytest.mark.parametrize(
    ("fan_mode", "wire_value"),
    [("auto", 0), ("speed_1", 4), ("speed_2", 5), ("speed_3", 6)],
)
async def test_ac_odd_numbered_fan_capabilities_and_commands(
    hass: HomeAssistant, cloud: FakeCloud, fan_mode: str, wire_value: int
) -> None:
    """Odd fan IDs describe numbered speeds; remote keys must not add phantom modes."""
    _make_ac(
        cloud,
        SupportedModes={"4": {"fanSpeeds": [1, 3, 5, 7]}},
        SupportedKeys=[8, 9, 10, 11],
    )
    cloud.state["modes"]["reported"]["mode"] = 3
    entry = hub_entry(subentries=[thermostat_subentry(pairing=None)])
    await setup_entry(hass, entry)
    assert hass.states.get(CLIMATE).attributes[ATTR_FAN_MODES] == [
        "auto",
        "speed_1",
        "speed_2",
        "speed_3",
    ]

    await _call(hass, SERVICE_SET_FAN_MODE, {ATTR_FAN_MODE: fan_mode})
    assert cloud.updates == [
        {"source": 3, "modes": {"fan_mode": wire_value, "mode": 3}}
    ]
    await entry.runtime_data.cloud.async_refresh()
    assert hass.states.get(CLIMATE).attributes[ATTR_FAN_MODE] == fan_mode

    with pytest.raises(ServiceValidationError):
        await _call(hass, SERVICE_SET_FAN_MODE, {ATTR_FAN_MODE: "medium"})
    assert len(cloud.updates) == 1
