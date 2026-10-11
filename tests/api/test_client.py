"""Tests for the cloud client and its models (no Home Assistant entities)."""

from __future__ import annotations

from datetime import UTC, datetime
import json
import time

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import pytest
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.mysa_local.api import (
    Capabilities,
    DeviceState,
    HeaterType,
    Mode,
    ModelFamily,
    MysaApiError,
    MysaAuth,
    MysaAuthError,
    MysaClient,
    MysaConnectionError,
    MysaInvalidCredentialsError,
    MysaNotFoundError,
    MysaRateLimitError,
    iso_utc,
    jwt_claims,
    model_family,
    value_range,
)
from custom_components.mysa_local.api.const import (
    API_BASE,
    COGNITO_URL,
    LEGACY_API_BASE,
)
from tests.common import (
    DEVICE_ID,
    EMAIL,
    PASSWORD,
    REFRESH_TOKEN,
    FakeCloud,
    load_json,
    make_jwt,
)


def _client(hass: HomeAssistant, token: str | None = REFRESH_TOKEN) -> MysaClient:
    session = async_get_clientsession(hass)
    auth = MysaAuth(session, token, user_agent="test")
    return MysaClient(session, auth, user_agent="test")


async def test_sign_in_with_srp(hass: HomeAssistant, cloud: FakeCloud) -> None:
    """The SRP exchange works end to end against the simulated Cognito."""
    session = async_get_clientsession(hass)
    seen: list[str] = []
    auth = MysaAuth(session, user_agent="test", on_refresh_token=seen.append)
    tokens = await auth.sign_in(EMAIL, PASSWORD)
    assert tokens.refresh_token == REFRESH_TOKEN
    assert auth.refresh_token == REFRESH_TOKEN
    assert seen == [REFRESH_TOKEN]
    assert auth.claims["email"] == EMAIL

    with pytest.raises(MysaInvalidCredentialsError):
        await MysaAuth(session, user_agent="test").sign_in(EMAIL, "nope")


async def test_refresh_only_when_needed(hass: HomeAssistant, cloud: FakeCloud) -> None:
    """One refresh, then the cached ID token until it nears expiry."""
    client = _client(hass)
    await client.get_states([DEVICE_ID])
    await client.get_states([DEVICE_ID])
    assert cloud.refresh_calls == 1
    headers = cloud.mock.mock_calls[-1][3]
    assert headers["authorization"].count(".") == 2  # raw JWT, no Bearer prefix
    assert headers["user-agent"] == "test"


async def test_refresh_failure_is_auth_error(
    hass: HomeAssistant, cloud: FakeCloud
) -> None:
    """An expired refresh token surfaces as MysaAuthError (-> reauth)."""
    cloud.refresh_ok = False
    with pytest.raises(MysaInvalidCredentialsError):
        await _client(hass).get_states([DEVICE_ID])
    with pytest.raises(MysaAuthError):
        await _client(hass, token=None).get_states([DEVICE_ID])


async def test_401_forces_one_refresh(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A 401 refreshes the token once and retries; a second 401 isn't a sign-in loss."""
    aioclient_mock.post(
        COGNITO_URL,
        json={"AuthenticationResult": {"IdToken": make_jwt(), "ExpiresIn": 3600}},
    )
    aioclient_mock.post(f"{API_BASE}/state/batch", status=401)
    with pytest.raises(MysaApiError) as err:
        await _client(hass).get_states([DEVICE_ID])
    assert not isinstance(err.value, MysaAuthError)
    assert err.value.status == 401
    assert [c[1].path for c in aioclient_mock.mock_calls].count("/state/batch") == 2


async def test_403_is_not_an_auth_error(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A 403 (unknown route, no permission) fails the request without a refresh."""
    aioclient_mock.post(
        COGNITO_URL,
        json={"AuthenticationResult": {"IdToken": make_jwt(), "ExpiresIn": 3600}},
    )
    aioclient_mock.get(f"{API_BASE}/capabilities/{DEVICE_ID}", status=403)
    with pytest.raises(MysaApiError) as err:
        await _client(hass).get_capabilities(DEVICE_ID)
    assert err.value.status == 403
    paths = [c[1].path for c in aioclient_mock.mock_calls]
    assert paths.count(f"/capabilities/{DEVICE_ID}") == 1
    assert len(paths) == 2  # one token refresh for the first call, no retry


@pytest.mark.parametrize(
    ("status", "error"),
    [
        (404, MysaNotFoundError),
        (429, MysaRateLimitError),
        (500, MysaConnectionError),
        (503, MysaConnectionError),
    ],
)
async def test_http_errors(
    hass: HomeAssistant, cloud: FakeCloud, status: int, error: type[Exception]
) -> None:
    """Status codes map to the right exceptions."""
    await _client(hass).get_states([DEVICE_ID])
    cloud.backend_status = status
    with pytest.raises(error):
        await _client(hass).get_states([DEVICE_ID])


async def test_writes(hass: HomeAssistant, cloud: FakeCloud) -> None:
    """Write bodies match what the Mysa app sends."""
    client = _client(hass)
    await client.update_state(DEVICE_ID, {"targetHeat": {"setpoint": 22.5}})
    assert cloud.updates == [{"source": 3, "targetHeat": {"setpoint": 22.5}}]
    await client.update_settings(DEVICE_ID, {"Format": "fahrenheit"})
    assert cloud.settings == [{"Format": "fahrenheit"}]
    state = await client.get_state(DEVICE_ID)
    assert state.heat_setpoint == 22.5


async def test_holds_and_schedules(hass: HomeAssistant, cloud: FakeCloud) -> None:
    """Hold, resume, create and delete schedules."""
    client = _client(hass)
    assert await client.get_hold(DEVICE_ID) is None
    with pytest.raises(MysaNotFoundError):  # no schedule to hold over
        await client.set_hold(DEVICE_ID, {"targetHeat": {"setpoint": 20}})
    await client.create_schedule(
        home_id="home", device_id=DEVICE_ID, device_type="BB", events=[]
    )
    until = datetime(2026, 10, 11, 7, 30, tzinfo=UTC)
    await client.set_hold(DEVICE_ID, {"targetHeat": {"setpoint": 20}}, until=until)
    assert cloud.hold is not None
    assert cloud.hold["holdType"] == "temporary"
    assert cloud.hold["endTime"] == "2026-10-11T07:30:00.000Z"
    start = datetime.fromisoformat(cloud.hold["startTime"].replace("Z", "+00:00"))
    assert start > datetime.now(UTC)  # Mysa rejects a start time in the past
    assert (await client.get_hold(DEVICE_ID)) == cloud.hold
    await client.resume_schedule(DEVICE_ID)
    assert cloud.hold is None
    await client.resume_schedule(DEVICE_ID)  # nothing to resume: fine

    assert len(await client.get_schedules("home")) == 1
    schedule_id = await client.create_schedule(
        home_id="home", device_id=DEVICE_ID, device_type="BB", events=[]
    )
    assert schedule_id == "s2"
    assert len(await client.get_schedules("home")) == 2
    await client.delete_schedule("s1", DEVICE_ID)
    await client.delete_schedule("s2", DEVICE_ID)
    assert cloud.schedules == []


async def test_reads(hass: HomeAssistant, cloud: FakeCloud) -> None:
    """Every read parses into its model."""
    client = _client(hass)
    boot = await client.bootstrap()
    assert boot.home_ids == ("00000000-0000-4000-8000-0000000000a1",)
    assert boot.devices[DEVICE_ID].name == "Living Room"
    records = await client.get_devices()
    assert records[DEVICE_ID].display_unit == "C"
    assert records[DEVICE_ID].duty_cycle_opt == 9
    caps = await client.get_capabilities(DEVICE_ID)
    assert caps.modes == (Mode.OFF, Mode.HEAT)
    firmware = await client.get_firmware_info(DEVICE_ID)
    assert not firmware.update_available
    days = await client.get_usage(
        DEVICE_ID, "America/Los_Angeles", datetime.now(UTC), datetime.now(UTC)
    )
    assert days[0].heat_seconds == 3600.5
    assert days[0].buckets[2].heat_seconds == 1800


def test_state_model() -> None:
    """Reported values win over desired ones; telemetry is parsed."""
    data = load_json("state_batch.json")[DEVICE_ID]["data"]
    data["targetHeat"]["desired"]["setpoint"] = 25  # not yet confirmed
    state = DeviceState.from_api(DEVICE_ID, data)
    assert state.heat_setpoint == 21.5
    assert state.mode is Mode.HEAT
    assert state.heating is True
    assert state.connected is True
    assert state.rssi == -55
    assert state.button_lock == 0
    assert state.control_type == 9
    assert state.setpoint_min == 5
    assert state.firmware == "3.17.5.13"
    assert state.in_demand_response is False
    assert state.setpoint_for(Mode.COOL) is None

    weird = DeviceState.from_api(
        DEVICE_ID, {"modes": {"reported": {"mode": 77}}, "x": 1}
    )
    assert weird.mode is None
    assert weird.heating is None


def test_capabilities_model() -> None:
    """Ranges come from validValues."""
    caps = Capabilities.from_api(load_json("capabilities_bb_v2_0_l.json"))
    assert caps.heat_setpoint_range == (5, 30, 0.5)
    assert caps.offset_range == (-5, 5, 0.5)
    assert caps.control_types == (8, 9)
    assert caps.lockout_values == (0, 3)
    assert caps.units == ("C", "F")
    assert caps.brightness
    assert caps.homekit
    assert not caps.matter
    assert not caps.humidity_sensor
    assert caps.scheduling
    assert caps.cool_setpoint_range is None
    assert value_range([]) is None
    assert value_range([1]) == (1, 1, 1.0)


async def test_state_history(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Changes come back sorted, with their source."""
    aioclient_mock.post(
        COGNITO_URL,
        json={"AuthenticationResult": {"IdToken": make_jwt(), "ExpiresIn": 3600}},
    )
    aioclient_mock.get(
        f"{API_BASE}/state/history/{DEVICE_ID}", json=load_json("state_history.json")
    )
    changes = await _client(hass).get_state_history(
        DEVICE_ID, datetime.now(UTC), datetime.now(UTC)
    )
    assert [c.field for c in changes][:3] == ["heatSetpoint", "mode", "mode"]
    assert changes[0].from_device
    assert changes[1].source == "app"


def _mock_cognito(aioclient_mock: AiohttpClientMocker) -> None:
    aioclient_mock.post(
        COGNITO_URL,
        json={"AuthenticationResult": {"IdToken": make_jwt(), "ExpiresIn": 3600}},
    )


async def test_add_device(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """The claim body matches the app's; 409 means already on the account."""
    _mock_cognito(aioclient_mock)
    aioclient_mock.post(f"{API_BASE}/devices", status=201, json={})
    client = _client(hass)
    assert await client.add_device(
        DEVICE_ID,
        home_id="home",
        model="BB-V2-0-L",
        time_zone="America/Los_Angeles",
        name="Hall",
        heater_type=HeaterType.FAN_FORCED,
    )
    body = json.loads(aioclient_mock.mock_calls[-1][2])
    assert body.pop("LastPaired") > 1_700_000_000
    assert body == {
        "Id": DEVICE_ID,
        "Name": "Hall",
        "Model": "BB-V2-0-L",
        "Home": "home",
        "TimeZone": "America/Los_Angeles",
        "Animation": "off",
        "ecoMode": 1,
        "Format": "celsius",
        "schedGlobalOffset": 0,
        "DutyCycleOpt": 8,
    }

    # Defaults: named after its ID and Baseboard; `extra` is merged in last.
    await client.add_device(
        DEVICE_ID,
        home_id="home",
        model="bb-v2-0-lte",  # an alias the app also accepts
        time_zone="UTC",
        fahrenheit=True,
        extra={"Animation": "on"},
    )
    body = json.loads(aioclient_mock.mock_calls[-1][2])
    assert (body["Name"], body["DutyCycleOpt"], body["Format"]) == (
        DEVICE_ID,
        9,
        "fahrenheit",
    )
    assert (body["schedGlobalOffset"], body["Animation"]) == (0, "on")


async def test_add_device_without_app_defaults(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """app_defaults=False sends the older app's body, which keeps settings."""
    _mock_cognito(aioclient_mock)
    aioclient_mock.post(f"{API_BASE}/devices", status=201, json={})
    client = _client(hass)
    for model, extra in (("BB-V2-0-L", {"schedGlobalOffset": 0}), ("INF-V1-0", {})):
        await client.add_device(
            DEVICE_ID,
            home_id="home",
            model=model,
            time_zone="UTC",
            name="Hall",
            fahrenheit=True,
            app_defaults=False,
        )
        body = json.loads(aioclient_mock.mock_calls[-1][2])
        assert body.pop("LastPaired") > 0
        assert body == {
            "Id": DEVICE_ID,
            "Name": "Hall",
            "Model": model,
            "Home": "home",
            "TimeZone": "UTC",
            **extra,
        }


@pytest.mark.parametrize(
    ("model", "expected"),
    [
        ("BB-V1-1", {"DutyCycleOpt": 0, "schedGlobalOffset": 1}),
        ("BB-V2-0", {"DutyCycleOpt": 9, "schedGlobalOffset": 1}),
        (
            "INF-V1-0",
            {
                "DutyCycleOpt": 0,
                "FloorOhms": 10,
                "FloorType": 1,
                "IsMysaFloorSensor": True,
                "MaxFloorTemp": 28,
                "MaxSetpoint": 28,
                "Tracking": 3,
                "schedGlobalOffset": 1,
            },
        ),
        ("AC-V1-0", {"SetupRequired": True, "schedGlobalOffset": 1}),
    ],
)
async def test_add_device_defaults_per_model(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    model: str,
    expected: dict[str, object],
) -> None:
    """Each Wi-Fi-only family gets the extra fields Mysa's app sends for it."""
    _mock_cognito(aioclient_mock)
    aioclient_mock.post(f"{API_BASE}/devices", status=201, json={})
    await _client(hass).add_device(
        DEVICE_ID, home_id="home", model=model, time_zone="UTC"
    )
    body = json.loads(aioclient_mock.mock_calls[-1][2])
    base = {"Id", "Name", "Model", "Home", "TimeZone", "LastPaired"}
    base |= {"Animation", "ecoMode", "Format"}
    assert {k: v for k, v in body.items() if k not in base} == expected


@pytest.mark.parametrize("model", ["BB-V3-0", "ST-V1-0", "LV", "CS-V1-0", "XX-V9"])
async def test_add_device_refuses_bluetooth_models(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, model: str
) -> None:
    """Models that need Bluetooth credentials (or unknown ones) are refused."""
    _mock_cognito(aioclient_mock)
    client = _client(hass)
    with pytest.raises(ValueError, match=model):
        await client.add_device(DEVICE_ID, home_id="home", model=model, time_zone="UTC")
    with pytest.raises(ValueError, match=model):
        await client.replace_device(
            DEVICE_ID, home_id="home", model=model, time_zone="UTC"
        )
    assert not aioclient_mock.mock_calls


async def test_add_device_conflict_and_errors(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """409 means already on the account; other errors propagate."""
    client = _client(hass)
    _mock_cognito(aioclient_mock)
    aioclient_mock.post(f"{API_BASE}/devices", status=409, json={"Id": DEVICE_ID})
    assert not await client.add_device(
        DEVICE_ID, home_id="home", model="BB-V2-0-L", time_zone="UTC"
    )

    aioclient_mock.clear_requests()
    _mock_cognito(aioclient_mock)
    aioclient_mock.post(f"{API_BASE}/devices", status=400, json={"message": "bad"})
    with pytest.raises(MysaApiError) as err:
        await client.add_device(
            DEVICE_ID, home_id="home", model="BB-V2-0-L", time_zone="UTC"
        )
    assert err.value.status == 400


async def test_replace_and_delete_device(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Purgeadd goes to the older host, where the old app sent it; delete doesn't."""
    _mock_cognito(aioclient_mock)
    aioclient_mock.post(f"{LEGACY_API_BASE}/devices/purgeadd", json={})
    aioclient_mock.delete(f"{API_BASE}/devices/{DEVICE_ID}", status=204)
    client = _client(hass)
    await client.replace_device(
        DEVICE_ID, home_id="home", model="BB-V2-0-L", time_zone="UTC"
    )
    method, url, data, _ = aioclient_mock.mock_calls[-1]
    assert (method, str(url)) == ("POST", f"{LEGACY_API_BASE}/devices/purgeadd")
    assert json.loads(data)["Id"] == DEVICE_ID
    await client.delete_device(DEVICE_ID)
    method, url, data, _ = aioclient_mock.mock_calls[-1]
    assert (method, str(url)) == ("DELETE", f"{API_BASE}/devices/{DEVICE_ID}")
    assert data is None


def test_helpers() -> None:
    """Small utilities."""
    assert (
        iso_utc(datetime(2026, 1, 2, 3, 4, 5, 678000, tzinfo=UTC))
        == "2026-01-02T03:04:05.678Z"
    )
    token = make_jwt(exp=time.time() + 10)
    assert jwt_claims(token)["sub"]
    assert model_family(" lv ") is ModelFamily.CENTRAL_HVAC
    assert model_family("bb-v2-0-l") is ModelFamily.BASEBOARD_LITE
    assert model_family(None) is None
    with pytest.raises(MysaAuthError):
        jwt_claims("not-a-jwt")
    assert json.loads(json.dumps({"ok": 1}))
