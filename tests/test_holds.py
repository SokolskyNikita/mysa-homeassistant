"""Hold mode selection and immediate commands, including an explicit OFF."""

from datetime import timedelta
from typing import Any
from unittest.mock import AsyncMock, patch

from aiohomekit.model.characteristics import CharacteristicsTypes
from aiohomekit.testing import FakeController
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
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


@pytest.mark.parametrize("timed", [False, True])
@pytest.mark.parametrize(
    ("initial_mode", "requested_mode", "expected_state"),
    [
        pytest.param(4, "off", {"modes": {"mode": 0}}, id="heat-to-off"),
        pytest.param(3, "off", {"modes": {"mode": 0}}, id="cool-to-off"),
        pytest.param(0, None, {"modes": {"mode": 0}}, id="preserve-off"),
        pytest.param(
            0,
            "heat",
            {"modes": {"mode": 4}, "targetHeat": {"setpoint": 19}},
            id="explicit-heat",
        ),
        pytest.param(
            4,
            "cool",
            {"modes": {"mode": 3}, "targetCool": {"setpoint": 19}},
            id="explicit-cool",
        ),
        pytest.param(
            3,
            None,
            {"modes": {"mode": 3}, "targetCool": {"setpoint": 19}},
            id="preserve-cool",
        ),
        pytest.param(
            None,
            None,
            {"modes": {"mode": 4}, "targetHeat": {"setpoint": 19}},
            id="unknown-defaults-to-heat",
        ),
    ],
)
async def test_cloud_hold_mode_and_commands(
    hass: HomeAssistant,
    cloud: FakeCloud,
    *,
    initial_mode: int | None,
    requested_mode: str | None,
    expected_state: dict[str, Any],
    timed: bool,
) -> None:
    """Hold payload and immediate commands preserve OFF and the selected target."""
    cloud.state["modes"]["reported"]["mode"] = initial_mode
    cloud.state["targetCool"] = {"reported": {"setpoint": 24}}
    cloud.schedules = [{"id": "existing", "deviceIds": [DEVICE_ID]}]
    await setup_entry(hass, hub_entry(subentries=[thermostat_subentry(pairing=None)]))
    data: dict[str, Any] = {"entity_id": CLIMATE, "temperature": 19}
    if requested_mode is not None:
        data["hvac_mode"] = requested_mode
    if timed:
        data["until"] = (dt_util.utcnow() + timedelta(hours=1)).isoformat()

    with patch("custom_components.mysa_local.cloud_coordinator.CLOUD_WRITE_SPACING", 0):
        await hass.services.async_call("mysa_local", "hold", data, blocking=True)

    assert cloud.hold is not None
    assert cloud.hold["state"] == expected_state
    assert cloud.hold["holdType"] == ("temporary" if timed else "permanent")
    expected_commands = []
    if expected_state["modes"]["mode"] != initial_mode:
        expected_commands.append({"source": 3, "modes": expected_state["modes"]})
    expected_commands.extend(
        {"source": 3, section: expected_state[section]}
        for section in ("targetHeat", "targetCool")
        if section in expected_state
    )
    assert cloud.updates == expected_commands
    assert (
        hass.states.get(CLIMATE).state
        == {0: "off", 3: "cool", 4: "heat"}[expected_state["modes"]["mode"]]
    )


@pytest.mark.parametrize("timed", [False, True])
@pytest.mark.parametrize(
    ("initial_mode", "requested_mode"),
    [(1, "off"), (2, "off"), (0, None)],
)
async def test_off_hold_only_changes_local_mode(
    hass: HomeAssistant,
    controller: FakeController,
    cloud: FakeCloud,
    *,
    initial_mode: int,
    requested_mode: str | None,
    timed: bool,
) -> None:
    """An OFF hold never sends a setpoint, even when a temperature is supplied."""
    pairing = controller.aliases[HKID]
    cloud.schedules = [{"id": "existing", "deviceIds": [DEVICE_ID]}]
    await setup_entry(hass, hub_entry())
    update_fake_characteristic(
        pairing, CharacteristicsTypes.HEATING_COOLING_TARGET, initial_mode
    )
    await hass.async_block_till_done()
    assert (
        hass.states.get(CLIMATE).state == {0: "off", 1: "heat", 2: "cool"}[initial_mode]
    )
    data: dict[str, Any] = {"entity_id": CLIMATE, "temperature": 19}
    if requested_mode is not None:
        data["hvac_mode"] = requested_mode
    if timed:
        data["until"] = (dt_util.utcnow() + timedelta(hours=1)).isoformat()

    with patch.object(
        pairing, "put_characteristics", AsyncMock(wraps=pairing.put_characteristics)
    ) as put:
        await hass.services.async_call("mysa_local", "hold", data, blocking=True)

    assert cloud.hold is not None
    assert cloud.hold["state"] == {"modes": {"mode": 0}}
    assert cloud.updates == []
    if initial_mode == 0:
        put.assert_not_awaited()
    else:
        aid, iid = fake_characteristic_address(
            pairing, CharacteristicsTypes.HEATING_COOLING_TARGET
        )
        put.assert_awaited_once_with([(aid, iid, 0)])
    assert hass.states.get(CLIMATE).state == "off"
    assert hass.states.get(CLIMATE).attributes["temperature"] == 21.5
