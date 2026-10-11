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
    Mode,
    MysaAuth,
    MysaAuthError,
    MysaClient,
    MysaConnectionError,
    MysaInvalidCredentialsError,
    MysaNotFoundError,
    MysaRateLimitError,
    iso_utc,
    jwt_claims,
    value_range,
)
from custom_components.mysa_local.api.const import API_BASE, COGNITO_URL
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
    """A 401 refreshes the token once and retries."""
    aioclient_mock.post(
        COGNITO_URL,
        json={"AuthenticationResult": {"IdToken": make_jwt(), "ExpiresIn": 3600}},
    )
    aioclient_mock.post(f"{API_BASE}/state/batch", status=401)
    with pytest.raises(MysaAuthError):
        await _client(hass).get_states([DEVICE_ID])
    assert [c[1].path for c in aioclient_mock.mock_calls].count("/state/batch") == 2


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

    assert await client.get_schedules("home") == []
    schedule_id = await client.create_schedule(
        home_id="home", device_id=DEVICE_ID, device_type="BB", events=[]
    )
    assert schedule_id == "s1"
    assert len(await client.get_schedules("home")) == 1
    await client.delete_schedule("s1", DEVICE_ID)
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


def test_helpers() -> None:
    """Small utilities."""
    assert (
        iso_utc(datetime(2026, 1, 2, 3, 4, 5, 678000, tzinfo=UTC))
        == "2026-01-02T03:04:05.678Z"
    )
    token = make_jwt(exp=time.time() + 10)
    assert jwt_claims(token)["sub"]
    with pytest.raises(MysaAuthError):
        jwt_claims("not-a-jwt")
    assert json.loads(json.dumps({"ok": 1}))
