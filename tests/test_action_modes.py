"""Holds and schedule events use only the modes the thermostat offers.

The allowed modes are the climate entity's own ``hvac_modes``. A refused mode
raises a translated ``ServiceValidationError`` before any cloud request.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
import pytest

from custom_components.mysa_local.schedule import async_hold, async_set_schedule

from .common import DEVICE_ID, FakeCloud, hub_entry, thermostat_subentry
from .conftest import setup_entry
from .test_new_cloud_models import configure
from .test_temperature_units import message

CLIMATE = "climate.living_room"
EXISTING = {"id": "existing", "deviceIds": [DEVICE_ID]}
HEATER_MODES = "off, heat"
AC_MODES = "off, heat, cool, fan_only"


def _make_ac(cloud: FakeCloud) -> None:
    """A Mini-Split AC offering off, heat, cool and fan only (no dry or auto)."""
    cloud.capabilities["features"]["climateControl"]["mode"]["validValues"] = [
        "off",
        "heat",
        "cool",
        "fanOnly",
    ]
    record = {
        "Model": "AC-V1-0",
        # Keys 3 and 4 are heat and cool on the AC's remote.
        "SupportedModes": {
            "3": {"temperatures": list(range(16, 31))},
            "4": {"temperatures": list(range(16, 31))},
        },
    }
    cloud.devices["DevicesObj"][DEVICE_ID].update(record)
    cloud.bootstrap["data"]["devices"][DEVICE_ID].update(record)


async def _setup(hass: HomeAssistant, cloud: FakeCloud, family: str) -> Any:
    cloud.schedules = [dict(EXISTING)]
    if family == "hvac":
        entry = hub_entry(subentries=[configure(cloud, "ST-V1-0")])
    else:
        if family == "ac":
            _make_ac(cloud)
        entry = hub_entry(subentries=[thermostat_subentry(pairing=None)])
    await setup_entry(hass, entry)
    return entry


async def _call(hass: HomeAssistant, service: str, **data: Any) -> None:
    with patch("custom_components.mysa_local.cloud_coordinator.CLOUD_WRITE_SPACING", 0):
        await hass.services.async_call(
            "mysa_local", service, {"entity_id": CLIMATE, **data}, blocking=True
        )


def _event(mode: str | None, temperature: float | None = 21) -> dict[str, Any]:
    event: dict[str, Any] = {"days": ["mon"], "time": "06:30"}
    if mode is not None:
        event["hvac_mode"] = mode
    if temperature is not None:
        event["temperature"] = temperature
    return event


def _assert_nothing_sent(cloud: FakeCloud, calls_before: int) -> None:
    assert cloud.hold is None
    assert cloud.updates == []
    assert cloud.schedules == [EXISTING]
    assert len(cloud.mock.mock_calls) == calls_before


@pytest.mark.parametrize(
    ("family", "mode", "allowed"),
    [
        *(
            pytest.param("heater", m, HEATER_MODES, id=f"heater-{m}")
            for m in ("cool", "fan_only", "dry", "heat_cool", "auto")
        ),
        *(
            pytest.param("ac", m, AC_MODES, id=f"ac-{m}")
            for m in ("dry", "heat_cool", "auto")
        ),
    ],
)
async def test_hold_refuses_a_mode_the_thermostat_lacks(
    hass: HomeAssistant, cloud: FakeCloud, family: str, mode: str, allowed: str
) -> None:
    """A hold names the refused mode and the allowed ones, and sends nothing."""
    await _setup(hass, cloud, family)
    calls_before = len(cloud.mock.mock_calls)
    with pytest.raises(ServiceValidationError) as err:
        await _call(hass, "hold", hvac_mode=mode)
    assert err.value.translation_key == "mode_not_supported"
    assert message(err.value) == (
        f"Living Room doesn't support {mode} mode, so a hold or schedule event "
        f"can't use it. Its modes are: {allowed}."
    )
    _assert_nothing_sent(cloud, calls_before)


@pytest.mark.parametrize(
    ("family", "data", "state"),
    [
        pytest.param(
            "heater",
            {"hvac_mode": "heat", "temperature": 20},
            {"modes": {"mode": 4}, "targetHeat": {"setpoint": 20}},
            id="heater-heat",
        ),
        pytest.param(
            "heater", {"hvac_mode": "off"}, {"modes": {"mode": 0}}, id="heater-off"
        ),
        pytest.param(
            "ac",
            {"hvac_mode": "cool", "temperature": 22},
            {"modes": {"mode": 3}, "targetCool": {"setpoint": 22}},
            id="ac-cool",
        ),
        pytest.param("ac", {"hvac_mode": "off"}, {"modes": {"mode": 0}}, id="ac-off"),
    ],
)
async def test_hold_in_a_supported_mode(
    hass: HomeAssistant,
    cloud: FakeCloud,
    family: str,
    data: dict[str, Any],
    state: dict[str, Any],
) -> None:
    """Modes the thermostat offers hold exactly as before."""
    await _setup(hass, cloud, family)
    await _call(hass, "hold", **data)
    assert cloud.hold is not None
    assert cloud.hold["state"] == state


async def test_hold_without_a_mode_is_not_checked_against_the_current_one(
    hass: HomeAssistant, cloud: FakeCloud
) -> None:
    """A hold that keeps the current mode works whatever that mode is."""
    cloud.state["modes"]["reported"]["mode"] = 0
    await _setup(hass, cloud, "heater")
    await _call(hass, "hold", temperature=20)
    assert cloud.hold is not None
    assert cloud.hold["state"] == {"modes": {"mode": 0}}


@pytest.mark.parametrize(
    "mode", ["off", "heat", "cool", "heat_cool", "fan_only", "dry", "auto"]
)
async def test_central_hvac_hold_is_not_implemented_for_any_mode(
    hass: HomeAssistant, cloud: FakeCloud, mode: str
) -> None:
    """Central HVAC has no holds yet; every mode gets that error, never a KeyError."""
    await _setup(hass, cloud, "hvac")
    calls_before = len(cloud.mock.mock_calls)
    with pytest.raises(HomeAssistantError, match="not implemented for this model"):
        await _call(hass, "hold", hvac_mode=mode)
    _assert_nothing_sent(cloud, calls_before)


@pytest.mark.parametrize(
    ("family", "events", "mode", "allowed"),
    [
        pytest.param(
            "heater", [_event("cool")], "cool", HEATER_MODES, id="heater-cool"
        ),
        pytest.param(
            "heater",
            [_event("heat"), _event("cool", 22)],
            "cool",
            HEATER_MODES,
            id="heater-second-event-cool",
        ),
    ],
)
async def test_schedule_refuses_a_mode_the_thermostat_lacks(
    hass: HomeAssistant,
    cloud: FakeCloud,
    *,
    family: str,
    events: list[dict[str, Any]],
    mode: str,
    allowed: str,
) -> None:
    """One event in a refused mode stops the whole schedule before any request."""
    await _setup(hass, cloud, family)
    calls_before = len(cloud.mock.mock_calls)
    with pytest.raises(ServiceValidationError) as err:
        await _call(hass, "set_schedule", events=events)
    assert err.value.translation_key == "mode_not_supported"
    assert message(err.value) == (
        f"Living Room doesn't support {mode} mode, so a hold or schedule event "
        f"can't use it. Its modes are: {allowed}."
    )
    _assert_nothing_sent(cloud, calls_before)


@pytest.mark.parametrize(
    ("family", "mode"),
    [
        *(
            pytest.param("heater", m, id=f"heater-{m}")
            for m in ("fan_only", "dry", "heat_cool", "auto", "bogus")
        ),
        *(
            pytest.param("ac", m, id=f"ac-{m}")
            for m in ("dry", "heat_cool", "auto", "bogus")
        ),
    ],
)
async def test_schedule_modes_outside_the_service_schema(
    hass: HomeAssistant, cloud: FakeCloud, family: str, mode: str
) -> None:
    """Called directly, any other mode is refused cleanly, never with a KeyError."""
    entry = await _setup(hass, cloud, family)
    thermostat = next(iter(entry.runtime_data.thermostats.values()))
    calls_before = len(cloud.mock.mock_calls)
    with pytest.raises(ServiceValidationError) as err:
        await async_set_schedule(thermostat, [_event(mode, None)])
    assert err.value.translation_key == "mode_not_supported"
    _assert_nothing_sent(cloud, calls_before)


async def test_hold_called_directly_with_an_unknown_mode(
    hass: HomeAssistant, cloud: FakeCloud
) -> None:
    """A mode name Home Assistant doesn't know is refused, never a ValueError."""
    entry = await _setup(hass, cloud, "heater")
    thermostat = next(iter(entry.runtime_data.thermostats.values()))
    with pytest.raises(ServiceValidationError) as err:
        await async_hold(hass, thermostat, None, "bogus", None)
    assert err.value.translation_key == "mode_not_supported"
    assert cloud.hold is None


@pytest.mark.parametrize(
    ("family", "events", "expected"),
    [
        pytest.param(
            "heater",
            [_event(None), _event("off")],
            [(4, 21.0), (0, None)],
            id="heater-heat-and-off",
        ),
        pytest.param(
            "ac",
            [_event("cool", 23), _event("heat", 20), _event("off")],
            [(3, 23.0), (4, 20.0), (0, None)],
            id="ac-cool-heat-off",
        ),
    ],
)
async def test_schedule_in_supported_modes(
    hass: HomeAssistant,
    cloud: FakeCloud,
    family: str,
    events: list[dict[str, Any]],
    expected: list[tuple[int, float | None]],
) -> None:
    """Events in modes the thermostat offers are stored as before."""
    await _setup(hass, cloud, family)
    cloud.schedules = []
    for i, event in enumerate(events):
        event["days"] = [("mon", "tue", "wed")[i]]
    await _call(hass, "set_schedule", events=events)
    (schedule,) = cloud.schedules
    stored = []
    for event in sorted(schedule["events"], key=lambda e: e["dayOfWeek"]):
        state = event["state"]
        target = state.get("targetCool") or state.get("targetHeat")
        stored.append((state["modes"]["mode"], target and target["setpoint"]))
    assert stored == expected


@pytest.mark.parametrize("mode", ["off", "heat", "cool"])
async def test_central_hvac_schedule_is_not_implemented_for_any_mode(
    hass: HomeAssistant, cloud: FakeCloud, mode: str
) -> None:
    """Central HVAC has no schedules yet; nothing is sent for any mode."""
    await _setup(hass, cloud, "hvac")
    calls_before = len(cloud.mock.mock_calls)
    with pytest.raises(HomeAssistantError, match="not implemented for this model"):
        await _call(hass, "set_schedule", events=[_event(mode)])
    _assert_nothing_sent(cloud, calls_before)
