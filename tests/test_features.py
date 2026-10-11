"""Actions, runtime and energy, repairs, diagnostics, firmware and removal."""

from __future__ import annotations

from datetime import timedelta
import json
from typing import Any
from unittest.mock import AsyncMock, patch

from aiohomekit.testing import FakeController
from freezegun.api import FrozenDateTimeFactory
from homeassistant.components.update import DOMAIN as UPDATE_DOMAIN, SERVICE_INSTALL
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import async_fire_time_changed
from pytest_homeassistant_custom_component.components.diagnostics import (
    get_diagnostics_for_config_entry,
)
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator

from custom_components.mysa_local.const import CONF_FIRMWARE_INSTALL, DOMAIN
from custom_components.mysa_local.schedule import build_events

from .common import (
    DEVICE_ID,
    HKID,
    HOME_ID,
    PAIRING_DATA,
    REFRESH_TOKEN,
    FakeCloud,
    hub_entry,
    thermostat_subentry,
)
from .conftest import setup_entry

CLIMATE = "climate.living_room"


def _thermostat(hass: HomeAssistant) -> Any:
    runtime = hass.config_entries.async_entries(DOMAIN)[0].runtime_data
    return next(iter(runtime.thermostats.values()))


@pytest.mark.usefixtures("controller")
async def test_hold_and_resume(hass: HomeAssistant, cloud: FakeCloud) -> None:
    """Hold until a time, then resume the schedule."""
    await setup_entry(hass, hub_entry())
    until = dt_util.utcnow() + timedelta(hours=2)
    with pytest.raises(HomeAssistantError, match="no Mysa schedule"):
        await hass.services.async_call(
            DOMAIN,
            "hold",
            {ATTR_ENTITY_ID: CLIMATE, "temperature": 19.5},
            blocking=True,
        )
    await hass.services.async_call(
        DOMAIN,
        "set_schedule",
        {
            ATTR_ENTITY_ID: CLIMATE,
            "events": [{"days": ["wed"], "time": "03:00", "temperature": 21}],
        },
        blocking=True,
    )
    await hass.services.async_call(
        DOMAIN,
        "hold",
        {ATTR_ENTITY_ID: CLIMATE, "temperature": 19.5, "until": until.isoformat()},
        blocking=True,
    )
    assert cloud.hold is not None
    assert cloud.hold["deviceId"] == DEVICE_ID
    assert cloud.hold["holdType"] == "temporary"
    assert cloud.hold["state"] == {
        "modes": {"mode": 4},
        "targetHeat": {"setpoint": 19.5},
    }
    # Applied right away (over HomeKit), not only when Mysa's hold starts.
    assert hass.states.get(CLIMATE).attributes["temperature"] == 19.5

    await hass.services.async_call(
        DOMAIN, "resume_schedule", {ATTR_ENTITY_ID: CLIMATE}, blocking=True
    )
    assert cloud.hold is None

    with pytest.raises(HomeAssistantError, match="past"):
        await hass.services.async_call(
            DOMAIN,
            "hold",
            {
                ATTR_ENTITY_ID: CLIMATE,
                "until": (dt_util.utcnow() - timedelta(hours=1)).isoformat(),
            },
            blocking=True,
        )


@pytest.mark.usefixtures("controller")
async def test_set_and_clear_schedule(hass: HomeAssistant, cloud: FakeCloud) -> None:
    """set_schedule replaces the Mysa schedule; clear_schedule deletes it."""
    await setup_entry(hass, hub_entry())
    events = [
        {"days": ["mon", "tue"], "time": "06:30", "temperature": 21},
        {"days": ["sun"], "time": "22:00", "temperature": 17},
    ]
    for _ in range(2):
        await hass.services.async_call(
            DOMAIN,
            "set_schedule",
            {ATTR_ENTITY_ID: CLIMATE, "events": events},
            blocking=True,
        )
    assert len(cloud.schedules) == 1  # the second call replaced the first
    schedule = cloud.schedules[0]
    assert schedule["homeId"] == HOME_ID
    assert schedule["deviceIds"] == [DEVICE_ID]
    assert schedule["deviceType"] == "BB"
    assert [(e["dayOfWeek"], e["executionTime"]) for e in schedule["events"]] == [
        ("0", "22:00:00"),
        ("1", "06:30:00"),
        ("2", "06:30:00"),
    ]
    assert schedule["events"][1]["state"] == {
        "modes": {"mode": 4},
        "targetHeat": {"setpoint": 21.0},
    }

    await hass.services.async_call(
        DOMAIN, "clear_schedule", {ATTR_ENTITY_ID: CLIMATE}, blocking=True
    )
    assert cloud.schedules == []


@pytest.mark.usefixtures("controller")
async def test_actions_need_cloud(hass: HomeAssistant) -> None:
    """Without the cloud the actions explain why they can't run."""
    await setup_entry(hass, hub_entry(signed_in=False))
    with pytest.raises(HomeAssistantError, match="cloud"):
        await hass.services.async_call(
            DOMAIN, "resume_schedule", {ATTR_ENTITY_ID: CLIMATE}, blocking=True
        )


def test_build_events_off_mode() -> None:
    """An off event carries no setpoint."""
    from datetime import time

    from custom_components.mysa_local.api import Mode

    events = build_events(
        [{"days": ["sat"], "time": time(8, 0), "hvac_mode": "off"}], Mode.HEAT
    )
    assert events[0]["state"] == {"modes": {"mode": 0}}
    assert events[0]["dayOfWeek"] == "6"


async def test_local_runtime_and_energy(
    hass: HomeAssistant, controller: FakeController, freezer: FrozenDateTimeFactory
) -> None:
    """Runtime counts while the relay is on; energy = runtime x wattage."""
    freezer.move_to("2026-10-10T12:00:00-07:00")  # well clear of midnight
    await setup_entry(
        hass, hub_entry(signed_in=False, subentries=[thermostat_subentry(watts=2000)])
    )
    freezer.tick(timedelta(minutes=30))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    runtime = float(hass.states.get("sensor.living_room_heating_runtime_today").state)
    assert runtime == pytest.approx(0.5, abs=0.02)
    assert (
        hass.states.get("sensor.living_room_heating_runtime_today").attributes["source"]
        == "local"
    )
    energy = float(hass.states.get("sensor.living_room_heater_energy_today").state)
    assert energy == pytest.approx(1.0, abs=0.05)

    # Relay off: the count stops.
    pairing = controller.aliases[HKID]
    pairing.testing.update_aid_iid([(1, 259, 0)])
    await hass.async_block_till_done()
    freezer.tick(timedelta(minutes=30))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert float(
        hass.states.get("sensor.living_room_heating_runtime_today").state
    ) == pytest.approx(0.5, abs=0.02)


@pytest.mark.usefixtures("controller")
async def test_cloud_runtime(
    hass: HomeAssistant, cloud: FakeCloud, freezer: FrozenDateTimeFactory
) -> None:
    """With the cloud on, the thermostat's own measured runtime is used."""
    freezer.move_to("2026-10-10T20:00:00+00:00")
    midnight = dt_util.start_of_local_day()
    cloud.usage[0]["T"] = int(midnight.timestamp())
    await setup_entry(hass, hub_entry())
    state = hass.states.get("sensor.living_room_heating_runtime_today")
    assert state.attributes["source"] == "cloud"
    assert float(state.state) == pytest.approx(3600.5 / 3600, abs=0.001)


async def test_local_down_repair_issue(
    hass: HomeAssistant, controller: FakeController, freezer: FrozenDateTimeFactory
) -> None:
    """A repair issue appears after 15 minutes down and clears on recovery."""
    await setup_entry(hass, hub_entry(signed_in=False))
    pairing = controller.aliases[HKID]
    pairing.available = False
    await _thermostat(hass).local.async_poll()
    issues = ir.async_get(hass)
    issue_id = f"local_unreachable_{DEVICE_ID}"

    freezer.tick(timedelta(minutes=10))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert issues.async_get_issue(DOMAIN, issue_id) is None

    freezer.tick(timedelta(minutes=6))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    issue = issues.async_get_issue(DOMAIN, issue_id)
    assert issue is not None
    assert issue.translation_key == "local_unreachable"

    pairing.available = True
    await _thermostat(hass).local.async_poll()
    freezer.tick(timedelta(minutes=1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert issues.async_get_issue(DOMAIN, issue_id) is None


@pytest.mark.usefixtures("controller")
async def test_diagnostics_redacted(
    hass: HomeAssistant, hass_client: ClientSessionGenerator, cloud: FakeCloud
) -> None:
    """Diagnostics carry what's needed for new models, and no secrets."""
    entry = hub_entry()
    await setup_entry(hass, entry)
    diag = await get_diagnostics_for_config_entry(hass, hass_client, entry)
    blob = json.dumps(diag)
    for secret in (
        REFRESH_TOKEN,
        PAIRING_DATA["iOSDeviceLTSK"],
        PAIRING_DATA["iOSPairingId"],
        "192.0.2.10",
        "user@example.com",
        "SERIAL0001",
        HOME_ID,
        DEVICE_ID,
        HKID,
    ):
        assert secret not in blob
    thermostat = diag["thermostats"][0]
    assert thermostat["model"] == "BB-V2-0-L"
    assert thermostat["cloud"]["capabilities"]["system"]["model"] == "BB-V2-0-L"
    assert thermostat["homekit"]["characteristics"]


@pytest.mark.usefixtures("controller")
async def test_firmware_install_is_opt_in(
    hass: HomeAssistant, cloud: FakeCloud
) -> None:
    """Install is unavailable unless allowed; when allowed it waits for completion."""
    cloud.firmware = {
        "update": True,
        "installedVersion": "3.17.5.13",
        "allowedVersion": "3.18.0.1",
    }
    await setup_entry(hass, hub_entry())
    state = hass.states.get("update.living_room_firmware")
    assert state.state == "on"
    assert state.attributes["latest_version"] == "3.18.0.1"
    assert state.attributes["supported_features"] == 0

    entry = hass.config_entries.async_entries(DOMAIN)[0]
    hass.config_entries.async_update_entry(
        entry, options={**entry.options, CONF_FIRMWARE_INSTALL: True}
    )
    await hass.async_block_till_done()
    assert hass.states.get("update.living_room_firmware").attributes[
        "supported_features"
    ]

    async def finish_soon(*_: Any) -> None:
        cloud.firmware = {
            "update": False,
            "installedVersion": "3.18.0.1",
            "allowedVersion": "3.18.0.1",
        }

    with (
        patch("custom_components.mysa_local.update.INSTALL_POLL", 0),
        patch(
            "custom_components.mysa_local.update.asyncio.sleep",
            AsyncMock(side_effect=finish_soon),
        ),
    ):
        await hass.services.async_call(
            UPDATE_DOMAIN,
            SERVICE_INSTALL,
            {ATTR_ENTITY_ID: "update.living_room_firmware"},
            blocking=True,
        )
    assert cloud.posts == [(f"/devices/{DEVICE_ID}/firmware-update", None)]


async def test_remove_subentry_unpairs(
    hass: HomeAssistant, controller: FakeController
) -> None:
    """Deleting a thermostat removes our HomeKit pairing from it."""
    entry = hub_entry(signed_in=False)
    await setup_entry(hass, entry)
    pairing = controller.aliases[HKID]
    (subentry_id,) = entry.subentries
    with patch.object(pairing, "remove_pairing", AsyncMock()) as remove:
        hass.config_entries.async_remove_subentry(entry, subentry_id)
        await hass.async_block_till_done()
    remove.assert_awaited_once_with(PAIRING_DATA["iOSPairingId"])
    assert hass.states.get(CLIMATE) is None


async def test_remove_entry_unpairs(
    hass: HomeAssistant, controller: FakeController
) -> None:
    """Deleting the integration unpairs every thermostat."""
    entry = hub_entry(signed_in=False)
    await setup_entry(hass, entry)
    with patch("custom_components.mysa_local.async_unpair", AsyncMock()) as unpair:
        await hass.config_entries.async_remove(entry.entry_id)
        await hass.async_block_till_done()
    unpair.assert_awaited_once()
    assert unpair.await_args.args[1]["iOSPairingId"] == PAIRING_DATA["iOSPairingId"]


@pytest.mark.usefixtures("controller")
async def test_humidity(hass: HomeAssistant, cloud: FakeCloud) -> None:
    """The cloud's humidity becomes a sensor and the climate's current humidity."""
    await setup_entry(hass, hub_entry())
    state = hass.states.get("sensor.living_room_humidity")
    assert state.state == "40.0"
    assert state.attributes["device_class"] == "humidity"
    assert hass.states.get(CLIMATE).attributes["current_humidity"] == 40.0


@pytest.mark.usefixtures("controller")
async def test_no_humidity_without_cloud(hass: HomeAssistant) -> None:
    """HomeKit has no humidity, so local-only setups don't get the sensor."""
    await setup_entry(hass, hub_entry(signed_in=False))
    assert hass.states.get("sensor.living_room_humidity") is None
    assert "current_humidity" not in hass.states.get(CLIMATE).attributes


@pytest.mark.usefixtures("controller")
async def test_heater_power(hass: HomeAssistant) -> None:
    """Heater power is the set wattage while the relay is on, else 0 W."""
    sub = thermostat_subentry()
    sub["data"] = {**sub["data"], "heater_watts": 1500}
    await setup_entry(hass, hub_entry(signed_in=False, subentries=[sub]))
    thermostat = _thermostat(hass)
    assert thermostat.local is not None
    from custom_components.mysa_local.local import CURRENT_STATE

    for relay, watts in ((1, "1500.0"), (0, "0.0")):
        thermostat.local.values[CURRENT_STATE] = relay
        thermostat.notify()
        await hass.async_block_till_done()
        assert hass.states.get("sensor.living_room_heater_power").state == watts


@pytest.mark.usefixtures("controller")
async def test_system_health(
    hass: HomeAssistant, cloud: FakeCloud, aioclient_mock: Any
) -> None:
    """System health shows the paths, the sign-in and whether Mysa is reachable."""
    from homeassistant.setup import async_setup_component
    from pytest_homeassistant_custom_component.common import get_system_health_info

    aioclient_mock.get("https://mysa-backend.mysa.cloud", text="")
    await setup_entry(hass, hub_entry(password="saved"))
    assert await async_setup_component(hass, "system_health", {})
    info = await get_system_health_info(hass, DOMAIN)
    for key, value in info.items():
        if hasattr(value, "__await__"):
            info[key] = await value
    assert info["thermostats"] == 1
    assert info["local"] == 1
    assert info["offline"] == 0
    assert info["signed_in"] is True
    assert info["password_saved"] is True
    assert info["cloud_last_update"] == "ok"
    assert info["can_reach_server"] == "ok"


@pytest.mark.usefixtures("controller")
async def test_system_health_cloud_off_makes_no_calls(
    hass: HomeAssistant, aioclient_mock: Any
) -> None:
    """With the cloud off, system health doesn't contact Mysa either."""
    from homeassistant.setup import async_setup_component
    from pytest_homeassistant_custom_component.common import get_system_health_info

    await setup_entry(hass, hub_entry(signed_in=False))
    assert await async_setup_component(hass, "system_health", {})
    info = await get_system_health_info(hass, DOMAIN)
    assert "can_reach_server" not in info
    assert info["signed_in"] is False
    assert aioclient_mock.call_count == 0
