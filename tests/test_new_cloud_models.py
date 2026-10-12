"""App-derived cloud-only models; fixtures are synthetic, not hardware captures."""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from unittest.mock import patch

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util
from mysa_client.cloud import Capabilities, DeviceState, Mode
import pytest

from custom_components.mysa_local.account import can_claim, can_pair_locally
from custom_components.mysa_local.const import CONF_MODEL
from custom_components.mysa_local.diagnostics import async_get_config_entry_diagnostics
from custom_components.mysa_local.schedule import async_resume_schedule

from .common import DEVICE_ID, FakeCloud, cloud_only_subentry, hub_entry, load_json
from .conftest import setup_entry

CLIMATE = "climate.living_room"
MODELS = ["BB-V3-0", "ST-V1-0", "CS-V1-0"]


def configure(cloud: FakeCloud, model: str) -> dict[str, Any]:
    """Load app-derived test data and give each reported field a current clock."""
    fixture = load_json("app_4201_new_models.json")["models"][model]
    cloud.capabilities = fixture["capabilities"]
    cloud.state = fixture["state"]
    now = dt_util.utcnow().timestamp()
    cloud.state["latestTelemetry"]["lastConnected"] = now
    cloud.state["latestTelemetry"]["reading"]["timestamp"] = now
    for node in cloud.state.values():
        if "reported" in node:
            node["reported"]["timestamp"] = now
    for records in (cloud.devices["DevicesObj"], cloud.bootstrap["data"]["devices"]):
        records[DEVICE_ID]["Model"] = model
    sub = cloud_only_subentry()
    sub["data"][CONF_MODEL] = model
    return sub


async def call(hass: HomeAssistant, service: str, **data: Any) -> None:
    await hass.services.async_call(
        "climate", service, {"entity_id": CLIMATE, **data}, blocking=True
    )


@pytest.mark.parametrize("model", MODELS)
async def test_imported_entities_and_no_extras(
    hass: HomeAssistant, cloud: FakeCloud, model: str
) -> None:
    """New models load without local pairing or any legacy supplementary requests."""
    sub = configure(cloud, model)
    entry = hub_entry(subentries=[sub])
    await setup_entry(hass, entry)
    thermostat = next(iter(entry.runtime_data.thermostats.values()))
    assert not can_pair_locally(model)
    assert not can_claim(DEVICE_ID, model)
    assert not thermostat.has_local
    assert hass.states.get("sensor.living_room_temperature").state == "21.5"
    assert hass.states.get("sensor.living_room_humidity").state == "42.0"
    assert bool(hass.states.get(CLIMATE)) == (model != "CS-V1-0")
    for entity_id in (
        "sensor.living_room_heating_runtime_today",
        "sensor.living_room_homekit_pairing",
        "update.living_room_firmware",
        "binary_sensor.living_room_schedule_hold",
        "select.living_room_heater_type",
        "number.living_room_temperature_offset",
    ):
        assert hass.states.get(entity_id) is None
    paths = [url.path for _, url, _, _ in cloud.mock.mock_calls]
    assert "/devices/state" not in paths
    assert not any(
        "/schedule" in path or "update_available" in path or "/usage/" in path
        for path in paths
    )
    diag = await async_get_config_entry_diagnostics(hass, entry)
    assert diag["thermostats"][0]["support_status"] == "experimental_cloud"
    assert DEVICE_ID not in str(diag)
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert bool(hass.states.get(CLIMATE)) == (model != "CS-V1-0")


async def test_v3_controls_and_delayed_confirmation(
    hass: HomeAssistant, cloud: FakeCloud
) -> None:
    """Accepted writes stay pending until matching reported data arrives."""
    entry = hub_entry(subentries=[configure(cloud, "BB-V3-0")])
    await setup_entry(hass, entry)
    coord = entry.runtime_data.cloud
    cloud.apply_updates = False
    await call(hass, "set_temperature", temperature=22)
    assert cloud.updates[-1] == {"source": 3, "targetHeat": {"setpoint": 22}}
    await coord.async_refresh()
    assert hass.states.get(CLIMATE).attributes["temperature"] == 20
    assert coord.pending[DEVICE_ID]["heat_setpoint"].status == "pending"
    cloud.state["targetHeat"]["reported"].update(
        setpoint=22, timestamp=dt_util.utcnow().timestamp()
    )
    await coord.async_refresh()
    assert coord.pending[DEVICE_ID]["heat_setpoint"].status == "confirmed"
    assert hass.states.get(CLIMATE).attributes["temperature"] == 22
    await call(hass, "set_hvac_mode", hvac_mode="off")
    assert cloud.updates[-1]["modes"] == {"mode": 0}
    thermostat = next(iter(entry.runtime_data.thermostats.values()))
    await thermostat.async_set_display_unit("F")
    assert cloud.updates[-1] == {"source": 3, "physicalInterface": {"format": "F"}}
    assert not cloud.settings
    with pytest.raises(HomeAssistantError, match="not implemented"):
        await async_resume_schedule(thermostat)


async def test_hvac_dual_setpoints_and_fan(
    hass: HomeAssistant, cloud: FakeCloud
) -> None:
    """HA heat_cool maps to targetAuto, with atomic validation before any write."""
    entry = hub_entry(subentries=[configure(cloud, "ST-V1-0")])
    await setup_entry(hass, entry)
    coord = entry.runtime_data.cloud
    state = hass.states.get(CLIMATE)
    assert state.state == "heat_cool"
    assert state.attributes["target_temp_low"] == 20
    assert state.attributes["target_temp_high"] == 24
    assert state.attributes["hvac_action"] == "cooling"
    await call(hass, "set_temperature", target_temp_low=21, target_temp_high=25)
    assert cloud.updates[-1] == {
        "source": 3,
        "targetAuto": {"heatSetpoint": 21, "coolSetpoint": 25},
    }
    await coord.async_refresh()
    assert coord.pending[DEVICE_ID]["auto_cool_setpoint"].status == "confirmed"
    previous = len(cloud.updates)
    with pytest.raises(HomeAssistantError):
        await call(
            hass,
            "set_temperature",
            hvac_mode="heat_cool",
            target_temp_low=22,
            target_temp_high=23,
        )
    assert len(cloud.updates) == previous
    await call(hass, "set_temperature", hvac_mode="cool", temperature=26)
    assert cloud.updates[-1] == {
        "source": 3,
        "modes": {"mode": 3},
        "targetCool": {"setpoint": 26},
    }
    await coord.async_refresh()
    await call(
        hass,
        "set_temperature",
        hvac_mode="heat_cool",
        target_temp_low=20,
        target_temp_high=24,
    )
    assert cloud.updates[-1]["targetAuto"] == {"heatSetpoint": 20, "coolSetpoint": 24}
    await call(hass, "set_fan_mode", fan_mode="on")
    assert cloud.updates[-1] == {
        "source": 3,
        "hvacConfig": {
            "advFanAlwaysOn": 1,
            "advFanRuntimeSeconds": 900,
            "advFanRuntimePeriodSeconds": 900,
        },
    }
    await coord.async_refresh()
    assert hass.states.get(CLIMATE).attributes["fan_mode"] == "on"
    await call(hass, "set_fan_mode", fan_mode="auto")
    assert cloud.updates[-1]["hvacConfig"] == {
        "advFanAlwaysOn": 0,
        "advFanRuntimeSeconds": 0,
        "advFanRuntimePeriodSeconds": 1800,
    }


async def test_hvac_activity_uses_own_clock(
    hass: HomeAssistant, cloud: FakeCloud
) -> None:
    """Packed stages and reported stage objects never borrow temperature freshness."""
    entry = hub_entry(subentries=[configure(cloud, "ST-V1-0")])
    await setup_entry(hass, entry)
    coord = entry.runtime_data.cloud
    thermostat = next(iter(entry.runtime_data.thermostats.values()))
    for code, action in [
        (256, "heating"),
        (4096, "heating"),
        (2048, "cooling"),
        (8192, "fan"),
        (0, "idle"),
        (256 | 1024, None),
    ]:
        cloud.state["latestTelemetry"]["reading"]["hvacState"] = code
        await coord.async_refresh()
        assert thermostat.action == action
    stages = dict.fromkeys(
        ("HeatingStage1", "HeatingStage2", "CoolingStage1", "CoolingStage2", "AuxHeat"),
        False,
    )
    stages["HeatingStage2"] = True
    cloud.state["modes"]["reported"].update(
        hvacStates=stages, timestamp=dt_util.utcnow().timestamp()
    )
    await coord.async_refresh()
    assert thermostat.action == "heating"
    future = dt_util.utcnow() + timedelta(hours=1)
    with patch("homeassistant.util.dt.utcnow", return_value=future):
        assert thermostat.action is None


@pytest.mark.parametrize("model", MODELS)
async def test_missing_capabilities_never_default_heat(
    hass: HomeAssistant, cloud: FakeCloud, model: str
) -> None:
    sub = configure(cloud, model)
    cloud.capabilities = {}
    entry = hub_entry(subentries=[sub])
    await setup_entry(hass, entry)
    assert hass.states.get(CLIMATE) is None
    thermostat = next(iter(entry.runtime_data.thermostats.values()))
    assert thermostat.supported_modes == []
    with pytest.raises(HomeAssistantError):
        await thermostat.async_set_mode(Mode.HEAT)
    assert not cloud.updates
    if model != "CS-V1-0":
        cloud.capabilities = load_json("app_4201_new_models.json")["models"][model][
            "capabilities"
        ]
        entry.runtime_data.cloud.mark_stale(f"caps:{DEVICE_ID}")
        await entry.runtime_data.cloud.async_refresh()
        await hass.async_block_till_done()
        assert hass.states.get(CLIMATE) is not None


async def test_new_model_cloud_error_and_missing_record(
    hass: HomeAssistant, cloud: FakeCloud
) -> None:
    """A missing records endpoint must not re-enable legacy endpoints."""
    sub = configure(cloud, "BB-V3-0")
    cloud.path_status["/devices"] = 500
    entry = hub_entry(subentries=[sub])
    await setup_entry(hass, entry)
    assert not cloud.calls("GET", "/devices/state")
    cloud.path_status[f"/state/{DEVICE_ID}/update"] = 403
    cloud.apply_updates = False
    with pytest.raises(HomeAssistantError):
        await call(hass, "set_temperature", temperature=22)
    assert hass.states.get(CLIMATE).attributes["temperature"] == 20
    coord = entry.runtime_data.cloud
    cloud.path_status["/state/batch"] = 500
    await coord.async_refresh()
    assert not coord.last_update_success


async def test_hvac_speed_uses_native_enum_and_pending_mode(
    hass: HomeAssistant, cloud: FakeCloud
) -> None:
    """Native auto speed is 9; current mode intent constrains subsequent changes."""
    entry = hub_entry(subentries=[configure(cloud, "ST-V1-0")])
    await setup_entry(hass, entry)
    thermostat = next(iter(entry.runtime_data.thermostats.values()))
    assert hass.states.get("select.living_room_fan_speed").state == "auto"
    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": "select.living_room_fan_speed", "option": "speed_2"},
        blocking=True,
    )
    assert cloud.updates[-1] == {"source": 3, "modes": {"fan_mode": 2}}
    await thermostat.async_set_hvac_fan_speed(9)
    assert cloud.updates[-1]["modes"] == {"fan_mode": 9}
    # Cooling has a different speed set; mode changes haven't been polled yet.
    cloud.capabilities["features"]["climateControl"]["cool"]["fan"]["fanSpeed"][
        "validValues"
    ] = [1]
    entry.runtime_data.cloud.mark_stale(f"caps:{DEVICE_ID}")
    await entry.runtime_data.cloud.async_refresh()
    await thermostat.async_set_mode(Mode.COOL)
    count = len(cloud.updates)
    with pytest.raises(HomeAssistantError, match="Fan speed"):
        await thermostat.async_set_hvac_fan_speed(2)
    assert len(cloud.updates) == count


async def test_hvac_mode_limits_disable_auto_and_sensor_writes(
    hass: HomeAssistant, cloud: FakeCloud
) -> None:
    """Reject targets by their own range, locked-out auto, and unsupported controls."""
    entry = hub_entry(subentries=[configure(cloud, "ST-V1-0")])
    await setup_entry(hass, entry)
    thermostat = next(iter(entry.runtime_data.thermostats.values()))
    for data in (
        {"mode": Mode.COOL, "temperature": 12},
        {"mode": Mode.HEAT, "temperature": 33},
        {"low": 21},
        {"temperature": 20},
    ):
        with pytest.raises(HomeAssistantError):
            await thermostat.async_set_basic_temperature(**data)
    assert not cloud.updates
    cloud.state["targetAuto"]["reported"].update(
        enabled=False, timestamp=dt_util.utcnow().timestamp()
    )
    await entry.runtime_data.cloud.async_refresh()
    assert "heat_cool" not in hass.states.get(CLIMATE).attributes["hvac_modes"]
    with pytest.raises(HomeAssistantError):
        await thermostat.async_set_mode(Mode.AUTO)
    with pytest.raises(HomeAssistantError, match="Legacy device settings"):
        await entry.runtime_data.cloud.async_update_settings(
            DEVICE_ID, {"DutyCycleOpt": 8}
        )
    assert not cloud.settings


@pytest.mark.usefixtures("controller")
async def test_mixed_account_preserves_lite_and_sensor_only(
    hass: HomeAssistant, cloud: FakeCloud
) -> None:
    """A Lite, V3, HVAC and sensor coexist without sharing capability assumptions."""
    from mysa_client.cloud import MysaClient

    from .common import thermostat_subentry

    scenarios = load_json("app_4201_new_models.json")["models"]
    subentries = [thermostat_subentry()]
    extra_states = {}
    extra_caps = {}
    for index, model in enumerate(MODELS):
        device_id = f"0a0b0c0d0e{index:02x}"
        sub = cloud_only_subentry(device_id)
        sub["data"][CONF_MODEL] = model
        sub["title"] = f"New device {index}"
        subentries.append(sub)
        cloud.devices["DevicesObj"][device_id] = {
            "Id": device_id,
            "Model": model,
            "Name": sub["title"],
        }
        state = scenarios[model]["state"]
        state["latestTelemetry"]["reading"]["timestamp"] = dt_util.utcnow().timestamp()
        state["latestTelemetry"]["lastConnected"] = dt_util.utcnow().timestamp()
        extra_states[device_id] = DeviceState.from_api(device_id, state)
        extra_caps[device_id] = Capabilities.from_api(scenarios[model]["capabilities"])
    original_states, original_caps = MysaClient.get_states, MysaClient.get_capabilities

    async def states(client: MysaClient, ids: Any) -> dict[str, DeviceState]:
        return {**await original_states(client, [DEVICE_ID]), **extra_states}

    async def capabilities(client: MysaClient, device_id: str) -> Capabilities:
        return (
            extra_caps[device_id]
            if device_id in extra_caps
            else await original_caps(client, device_id)
        )

    entry = hub_entry(subentries=subentries)
    with (
        patch.object(MysaClient, "get_states", states),
        patch.object(MysaClient, "get_capabilities", capabilities),
    ):
        await setup_entry(hass, entry)
        assert len(hass.states.async_all("climate")) == 3
        assert hass.states.get("climate.new_device_2") is None
        assert hass.states.get("climate.new_device_1").state == "heat_cool"
        assert hass.states.get("climate.new_device_0").attributes["hvac_modes"] == [
            "off",
            "heat",
        ]
        assert hass.states.get("sensor.living_room_homekit_pairing").state == "paired"
        assert hass.states.get("sensor.new_device_2_humidity") is not None
        for index in range(3):
            assert not cloud.calls(
                "GET", f"/devices/update_available/0a0b0c0d0e{index:02x}"
            )
            assert not cloud.calls("GET", f"/schedule/hold/0a0b0c0d0e{index:02x}")
        await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
        assert len(hass.states.async_all("climate")) == 3


@pytest.mark.parametrize("model", MODELS)
async def test_account_import_skips_local_pairing(
    hass: HomeAssistant, cloud: FakeCloud, model: str
) -> None:
    """Full account setup offers each new model, then the untested-model notice."""
    from homeassistant import config_entries

    from custom_components.mysa_local.const import DOMAIN

    from .common import EMAIL, PASSWORD

    configure(cloud, model)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "cloud_login"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"email": EMAIL, "password": PASSWORD}
    )
    assert result["step_id"] == "cloud_devices"
    with patch("custom_components.mysa_local.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"devices": [DEVICE_ID]}
        )
        assert result["step_id"] == "finish"
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
        assert result["type"] == "create_entry"
        entry = result["result"]
        assert next(iter(entry.subentries.values())).data[CONF_MODEL] == model
    assert not cloud.claims


async def test_partial_capabilities_do_not_advertise_targets(
    hass: HomeAssistant, cloud: FakeCloud
) -> None:
    """An off-only capability cannot accidentally advertise heat/setpoint controls."""
    from homeassistant.components.climate import ClimateEntityFeature

    sub = configure(cloud, "BB-V3-0")
    del cloud.capabilities["features"]["climateControl"]["heat"]
    entry = hub_entry(subentries=[sub])
    await setup_entry(hass, entry)
    attrs = hass.states.get(CLIMATE).attributes
    assert attrs["hvac_modes"] == ["off"]
    features = ClimateEntityFeature(attrs["supported_features"])
    assert features & ClimateEntityFeature.TURN_OFF
    assert not features & (
        ClimateEntityFeature.TURN_ON | ClimateEntityFeature.TARGET_TEMPERATURE
    )


async def test_new_model_reconfigure_explains_scope(
    hass: HomeAssistant, cloud: FakeCloud
) -> None:
    """Cloud-only setup has an informative menu rather than legacy settings."""
    from homeassistant import config_entries

    entry = hub_entry(subentries=[configure(cloud, "BB-V3-0")])
    await setup_entry(hass, entry)
    subentry = next(iter(entry.subentries.values()))
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "thermostat"),
        context={
            "source": config_entries.SOURCE_RECONFIGURE,
            "subentry_id": subentry.subentry_id,
        },
    )
    assert result["menu_options"] == ["cloud_info", "mqtt"]
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"next_step_id": "cloud_info"}
    )
    assert result["step_id"] == "cloud_info"
    result = await hass.config_entries.subentries.async_configure(result["flow_id"], {})
    assert result["reason"] == "cloud_info"
