"""Setpoints in the owner's unit: native °F for Fahrenheit owners, °C otherwise.

Every written value goes through mysa-client's SetpointRules. The fakes refuse
anything off the 0.5 °C grid, so a write path that skips the rules fails here.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

from aiohomekit.model.characteristics import CharacteristicsTypes
from aiohomekit.testing import FakeController
from homeassistant.const import (
    EVENT_LOGBOOK_ENTRY,
    EVENT_STATE_CHANGED,
    EVENT_STATE_REPORTED,
    UnitOfTemperature,
)
from homeassistant.core import Context, HomeAssistant, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.util.unit_system import METRIC_SYSTEM, US_CUSTOMARY_SYSTEM
from mysa_client.cloud import fahrenheit_label
from mysa_client.cloud.const import API_BASE
from mysa_client.homekit import Field
import pytest

from custom_components.mysa_local.const import DOMAIN

from .common import (
    DEVICE_ID,
    HKID,
    FakeCloud,
    fake_characteristic_address,
    hub_entry,
    thermostat_subentry,
)
from .conftest import setup_entry
from .harness import (
    MockUser,
    async_capture_events,
)
from .test_new_cloud_models import configure

CLIMATE = "climate.living_room"
LOWEST = "number.living_room_lowest_setpoint"
HIGHEST = "number.living_room_highest_setpoint"
OFFSET = "number.living_room_temperature_offset"
MESSAGES = json.loads(
    (
        Path(__file__).parents[1] / "custom_components/mysa_local/translations/en.json"
    ).read_text()
)["exceptions"]


def use_units(hass: HomeAssistant, fahrenheit: bool) -> None:
    hass.config.units = US_CUSTOMARY_SYSTEM if fahrenheit else METRIC_SYSTEM


def cloud_only() -> Any:
    return hub_entry(subentries=[thermostat_subentry(pairing=None)])


def report(cloud: FakeCloud, section: str, **fields: Any) -> None:
    cloud.state[section]["reported"].update(fields)
    cloud.state[section].setdefault("desired", {}).update(fields)


async def call(
    hass: HomeAssistant,
    domain: str,
    service: str,
    *,
    context: Context | None = None,
    **data: Any,
) -> None:
    await hass.services.async_call(
        domain, service, data, blocking=True, context=context
    )
    await hass.async_block_till_done()


async def set_temperature(
    hass: HomeAssistant, *, user: MockUser | None = None, **data: Any
) -> None:
    """Set a target; with ``user``, as that person on the card would."""
    context = Context(user_id=user.id) if user else None
    await call(
        hass, "climate", "set_temperature", context=context, entity_id=CLIMATE, **data
    )


def attrs(hass: HomeAssistant) -> dict[str, Any]:
    return dict(hass.states.get(CLIMATE).attributes)


def message(err: ServiceValidationError) -> str:
    """The translated message, as the frontend shows it."""
    assert err.translation_domain == DOMAIN
    template = MESSAGES[err.translation_key]["message"]
    return template.format(**(err.translation_placeholders or {}))


# ---- display ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("fahrenheit", "native_step", "expected_step"),
    [
        (False, 0.5, 0.5),
        (True, 0.5, 1.0),
        (False, 1.0, 1.0),
        (True, 1.0, 1.0),
        (True, 0.1, 1.0),
    ],
)
async def test_display_step_and_bounds_follow_ha_units(
    hass: HomeAssistant,
    cloud: FakeCloud,
    fahrenheit: bool,
    native_step: float,
    expected_step: float,
) -> None:
    """°F owners pick whole degrees; °C owners use the thermostat's own step."""
    use_units(hass, fahrenheit)
    cloud.capabilities["features"]["climateControl"]["heat"]["setpoint"][
        "validValues"
    ] = [5, 5 + native_step, 30]
    # The thermostat screen deliberately uses the opposite unit to HA.
    screen_unit = "celsius" if fahrenheit else "fahrenheit"
    cloud.devices["DevicesObj"][DEVICE_ID]["Format"] = screen_unit
    cloud.bootstrap["data"]["devices"][DEVICE_ID]["Format"] = screen_unit
    await setup_entry(hass, cloud_only())
    state = attrs(hass)
    assert state["target_temp_step"] == expected_step
    assert state["min_temp"] == (41 if fahrenheit else 5)
    assert state["max_temp"] == (86 if fahrenheit else 30)
    assert hass.states.get("select.living_room_display_units").state == screen_unit


@pytest.mark.parametrize(
    ("fahrenheit", "target", "shown"),
    [(True, 22.5, 73), (True, 22.0, 72), (True, 7.5, 46), (False, 22.5, 22.5)],
)
async def test_targets_show_like_the_mysa_app(
    hass: HomeAssistant,
    cloud: FakeCloud,
    *,
    fahrenheit: bool,
    target: float,
    shown: float,
) -> None:
    """Whole °F rounded halfway up (22.5 °C is 73, not Python's 72)."""
    use_units(hass, fahrenheit)
    report(cloud, "targetHeat", setpoint=target)
    await setup_entry(hass, cloud_only())
    state = attrs(hass)
    assert state["temperature"] == shown
    runtime = hass.config_entries.async_entries(DOMAIN)[0].runtime_data
    room = next(iter(runtime.thermostats.values())).current_temperature
    assert room is not None
    expected_room = round(room * 1.8 + 32, 1) if fahrenheit else room
    assert state["current_temperature"] == expected_room


async def test_card_top_matches_the_label_of_an_in_between_limit(
    hass: HomeAssistant, cloud: FakeCloud
) -> None:
    """A 22.5 °C highest setpoint shows as 73 °F, so the card goes up to 73."""
    use_units(hass, True)
    report(cloud, "targetHeat", setpoint=21, lockoutMax=22.5)
    entry = cloud_only()
    await setup_entry(hass, entry)
    # 73 °F alone is 22.8 °C, which rounds past the limit; 72 was the old top.
    assert attrs(hass)["max_temp"] == 73
    assert hass.states.get(HIGHEST).state == "73"

    logbook = async_capture_events(hass, EVENT_LOGBOOK_ENTRY)
    await set_temperature(hass, temperature=73)
    # Held at the limit, which is what 73 °F shows: nothing to tell the owner.
    assert cloud.updates == [{"source": 3, "targetHeat": {"setpoint": 22.5}}]
    assert logbook == []
    await entry.runtime_data.cloud.async_refresh()
    await hass.async_block_till_done()
    assert attrs(hass)["temperature"] == 73

    with pytest.raises(ServiceValidationError):
        await set_temperature(hass, temperature=74)


@pytest.mark.parametrize("local", [False, True])
async def test_fahrenheit_limits_give_whole_card_bounds(
    hass: HomeAssistant, controller: FakeController, cloud: FakeCloud, local: bool
) -> None:
    """Limits of 18-21.5 °C allow 64-71 °F; HA itself refuses 72."""
    use_units(hass, True)
    report(cloud, "targetHeat", lockoutMin=18, lockoutMax=21.5)
    pairing = controller.aliases[HKID]
    entry = hub_entry() if local else cloud_only()
    await setup_entry(hass, entry)
    assert (attrs(hass)["min_temp"], attrs(hass)["max_temp"]) == (64, 71)
    assert hass.states.get(LOWEST).state == "64"
    assert hass.states.get(HIGHEST).state == "71"
    assert hass.states.get(HIGHEST).attributes["unit_of_measurement"] == "°F"

    with patch.object(
        pairing, "put_characteristics", AsyncMock(wraps=pairing.put_characteristics)
    ) as put:
        # The thermostat is at 21.5 °C (71 °F): step away first, since picking
        # the shown label again sends nothing.
        await set_temperature(hass, temperature=70)
        if not local:
            await entry.runtime_data.cloud.async_refresh()
            await hass.async_block_till_done()
        await set_temperature(hass, temperature=71)
        if local:
            aid, iid = fake_characteristic_address(
                pairing, CharacteristicsTypes.TEMPERATURE_TARGET
            )
            assert put.await_args_list[-1].args == ([(aid, iid, 21.5)],)
        else:
            assert cloud.updates[-1] == {"source": 3, "targetHeat": {"setpoint": 21.5}}
            await entry.runtime_data.cloud.async_refresh()
            await hass.async_block_till_done()
        assert attrs(hass)["temperature"] == 71
        writes = put.await_count, len(cloud.updates)
        for outside in (63, 72):
            with pytest.raises(ServiceValidationError) as err:
                await set_temperature(hass, temperature=outside)
            # HA's own range check, before the integration is called.
            assert err.value.translation_domain == "climate"
            assert err.value.translation_key == "temp_out_of_range"
        assert (put.await_count, len(cloud.updates)) == writes


async def test_every_whole_fahrenheit_round_trips(
    hass: HomeAssistant, controller: FakeController
) -> None:
    """Each whole °F from 41 to 86 is written on the grid and shows as picked."""
    use_units(hass, True)
    pairing = controller.aliases[HKID]
    await setup_entry(hass, hub_entry(signed_in=False))
    aid, iid = fake_characteristic_address(
        pairing, CharacteristicsTypes.TEMPERATURE_TARGET
    )
    with patch.object(
        pairing, "put_characteristics", AsyncMock(wraps=pairing.put_characteristics)
    ) as put:
        for fahrenheit in range(41, 87):
            await set_temperature(hass, temperature=fahrenheit)
            written = put.await_args.args[0][0][2]
            assert put.await_args.args[0][0][:2] == (aid, iid)
            assert written * 2 == round(written * 2), fahrenheit
            assert fahrenheit_label(written) == fahrenheit
            assert attrs(hass)["temperature"] == fahrenheit
    assert not getattr(pairing, "refused_writes", [])


@pytest.mark.parametrize(
    ("fahrenheit", "requested", "expected_celsius"),
    [
        (True, 73, 23.0),
        (True, 72, 22.0),
        (True, 70.7, 21.5),
        (False, 21.3, 21.5),
        (False, 21.2, 21.0),
        (False, 21.25, 21.5),
    ],
)
async def test_setpoint_rounds_to_thermostat_step(
    hass: HomeAssistant,
    controller: FakeController,
    cloud: FakeCloud,
    *,
    fahrenheit: bool,
    requested: float,
    expected_celsius: float,
) -> None:
    """Off-step targets are rounded (halfway up) to the step the thermostat accepts."""
    use_units(hass, fahrenheit)
    pairing = controller.aliases[HKID]
    await setup_entry(hass, hub_entry())
    with patch.object(
        pairing, "put_characteristics", AsyncMock(wraps=pairing.put_characteristics)
    ) as put:
        await set_temperature(hass, temperature=requested)
    aid, iid = fake_characteristic_address(
        pairing, CharacteristicsTypes.TEMPERATURE_TARGET
    )
    put.assert_awaited_once_with([(aid, iid, expected_celsius)])


async def test_unit_switch_applies_at_once(
    hass: HomeAssistant, cloud: FakeCloud
) -> None:
    """Switching HA's unit system rewrites every Mysa entity straight away."""
    report(cloud, "targetHeat", setpoint=22.5, lockoutMax=21.5)
    await setup_entry(hass, cloud_only())
    assert attrs(hass)["temperature"] == 22.5
    assert hass.states.get(HIGHEST).state == "21.5"

    await hass.config.async_update(unit_system="us_customary")
    await hass.async_block_till_done()
    assert attrs(hass)["temperature"] == 73
    assert attrs(hass)["target_temp_step"] == 1
    assert hass.states.get(HIGHEST).state == "71"
    assert hass.states.get(HIGHEST).attributes["unit_of_measurement"] == "°F"

    await hass.config.async_update(unit_system="metric")
    await hass.async_block_till_done()
    assert attrs(hass)["temperature"] == 22.5
    assert hass.states.get(HIGHEST).attributes["unit_of_measurement"] == "°C"


async def test_state_is_written_during_and_after_the_call(
    hass: HomeAssistant, controller: FakeController
) -> None:
    """Scripts see the new state at once; one more write follows the call.

    The frontend ignores updates while its own call is in flight, so the
    write after the call is the one that can reach it.
    """
    use_units(hass, True)
    await setup_entry(hass, hub_entry(signed_in=False))
    await hass.services.async_call(
        "climate",
        "set_temperature",
        {"entity_id": CLIMATE, "temperature": 75},
        blocking=True,
    )
    # Written during the call, so no wait is needed to see it.
    assert attrs(hass)["temperature"] == 75
    reported: list[str] = []

    @callback
    def climate_only(data: Any) -> bool:
        return data["entity_id"] == CLIMATE

    @callback
    def record(event: Any) -> None:
        reported.append(event.data["entity_id"])

    hass.bus.async_listen(EVENT_STATE_REPORTED, record, event_filter=climate_only)
    changed = async_capture_events(hass, EVENT_STATE_CHANGED)
    await hass.async_block_till_done()
    assert reported == [CLIMATE]
    assert changed == []


# ---- clamping and errors -----------------------------------------------------


@pytest.mark.parametrize(
    ("fahrenheit", "requested", "written", "text"),
    [
        (
            True,
            91,
            30.0,
            (
                "was set to 86 °F instead of the requested 91 °F, "
                "the highest setpoint it allows"
            ),
        ),
        (
            False,
            33,
            30.0,
            (
                "was set to 30 °C instead of the requested 33 °C, "
                "the highest setpoint it allows"
            ),
        ),
    ],
)
async def test_card_clamps_to_the_mode_range_and_logs_it(
    hass: HomeAssistant,
    cloud: FakeCloud,
    *,
    fahrenheit: bool,
    requested: float,
    written: float,
    text: str,
) -> None:
    """Central HVAC's card spans every mode; heat is clamped to its own range."""
    use_units(hass, fahrenheit)
    await setup_entry(hass, hub_entry(subentries=[configure(cloud, "ST-V1-0")]))
    # The card's envelope reaches the cool maximum of 35 °C.
    assert attrs(hass)["max_temp"] == (95 if fahrenheit else 35)
    logbook = async_capture_events(hass, EVENT_LOGBOOK_ENTRY)
    await set_temperature(hass, hvac_mode="heat", temperature=requested)
    assert cloud.updates[-1] == {
        "source": 3,
        "modes": {"mode": 4},
        "targetHeat": {"setpoint": written},
    }
    assert [event.data for event in logbook] == [
        {
            "name": "Living Room",
            "message": text,
            "entity_id": CLIMATE,
            "domain": DOMAIN,
        }
    ]


@pytest.mark.parametrize(
    ("fahrenheit", "low", "high", "text"),
    [
        (
            True,
            70,
            72,
            (
                "The heating target (70 °F) and cooling target (72 °F) "
                "must be at least 3.6 °F apart."
            ),
        ),
        (
            False,
            21,
            22.5,
            (
                "The heating target (21 °C) and cooling target (22.5 °C) "
                "must be at least 2 °C apart."
            ),
        ),
    ],
)
async def test_heat_cool_pair_needs_the_minimum_gap(
    hass: HomeAssistant,
    cloud: FakeCloud,
    *,
    fahrenheit: bool,
    low: float,
    high: float,
    text: str,
) -> None:
    """A pair closer than the device allows is refused, never moved apart."""
    use_units(hass, fahrenheit)
    await setup_entry(hass, hub_entry(subentries=[configure(cloud, "ST-V1-0")]))
    with pytest.raises(ServiceValidationError) as err:
        await set_temperature(hass, target_temp_low=low, target_temp_high=high)
    assert message(err.value) == text
    assert cloud.updates == []


async def test_central_hvac_rounds_each_target_to_its_own_step(
    hass: HomeAssistant, cloud: FakeCloud
) -> None:
    """Single and dual targets use their own mode's range and step."""
    use_units(hass, True)
    sub = configure(cloud, "ST-V1-0")
    # Give cool a whole-degree step to tell the per-mode steps apart.
    cloud.capabilities["features"]["climateControl"]["cool"]["setpoint"][
        "validValues"
    ] = [float(v) for v in range(16, 36)]
    entry = hub_entry(subentries=[sub])
    await setup_entry(hass, entry)
    await set_temperature(hass, target_temp_low=70, target_temp_high=76)
    # 70 °F is 21.11 °C and 76 °F is 24.44 °C.
    assert cloud.updates[-1] == {
        "source": 3,
        "targetAuto": {"heatSetpoint": 21.0, "coolSetpoint": 24.5},
    }
    await entry.runtime_data.cloud.async_refresh()
    await hass.async_block_till_done()
    assert (attrs(hass)["target_temp_low"], attrs(hass)["target_temp_high"]) == (
        70,
        76,
    )
    await set_temperature(hass, hvac_mode="cool", temperature=78)
    # 78 °F is 25.56 °C; cool's 1 °C step makes it 26.
    assert cloud.updates[-1]["targetCool"] == {"setpoint": 26.0}
    await entry.runtime_data.cloud.async_refresh()
    # 26 °C shows as 79 °F, so picking 79 again sends nothing.
    sent = len(cloud.updates)
    await set_temperature(hass, temperature=79)
    assert len(cloud.updates) == sent
    await set_temperature(hass, hvac_mode="heat", temperature=79)
    # Heat keeps its 0.5 °C step.
    assert cloud.updates[-1]["targetHeat"] == {"setpoint": 26.0}
    await set_temperature(hass, hvac_mode="heat", temperature=80)
    assert cloud.updates[-1]["targetHeat"] == {"setpoint": 26.5}


@pytest.mark.parametrize(
    ("fahrenheit", "requested", "expected"),
    [(True, 73, 23.0), (True, 72, 22.0), (False, 21.3, 21.5), (False, 21.2, 21.0)],
)
async def test_newer_cloud_heater_rounds_its_target(
    hass: HomeAssistant,
    cloud: FakeCloud,
    *,
    fahrenheit: bool,
    requested: float,
    expected: float,
) -> None:
    """Newer cloud-only models skip the routed path but still use the rules."""
    use_units(hass, fahrenheit)
    await setup_entry(hass, hub_entry(subentries=[configure(cloud, "BB-V3-0")]))
    await set_temperature(hass, temperature=requested)
    assert cloud.updates == [{"source": 3, "targetHeat": {"setpoint": expected}}]


# ---- holds and schedules -------------------------------------------------------


@pytest.mark.parametrize(
    ("fahrenheit", "requested", "expected_celsius"),
    # Always °C (#48): a Fahrenheit owner's 21 is 21 °C, as before 0.9.0.
    [(True, 22.78, 23.0), (True, 21, 21.0), (False, 22.78, 23.0), (False, 19, 19)],
)
async def test_hold_takes_celsius_whatever_the_unit_system(
    hass: HomeAssistant,
    cloud: FakeCloud,
    *,
    fahrenheit: bool,
    requested: float,
    expected_celsius: float,
) -> None:
    """A hold rounds both its payload and the immediate setpoint command."""
    use_units(hass, fahrenheit)
    cloud.schedules = [{"id": "existing", "deviceIds": [DEVICE_ID]}]
    await setup_entry(hass, cloud_only())
    with patch("custom_components.mysa_local.cloud_coordinator.CLOUD_WRITE_SPACING", 0):
        await call(hass, DOMAIN, "hold", entity_id=CLIMATE, temperature=requested)
    assert cloud.hold is not None
    assert cloud.hold["state"]["targetHeat"] == {"setpoint": expected_celsius}
    assert cloud.updates == [
        {"source": 3, "targetHeat": {"setpoint": expected_celsius}}
    ]


async def test_hold_resends_an_off_step_current_target_rounded(
    hass: HomeAssistant, cloud: FakeCloud
) -> None:
    """A hold without a temperature re-sends the current one, on the grid."""
    cloud.schedules = [{"id": "existing", "deviceIds": [DEVICE_ID]}]
    report(cloud, "targetHeat", setpoint=21.3)
    await setup_entry(hass, cloud_only())
    with patch("custom_components.mysa_local.cloud_coordinator.CLOUD_WRITE_SPACING", 0):
        await call(hass, DOMAIN, "hold", entity_id=CLIMATE, hvac_mode="heat")
    assert cloud.hold["state"]["targetHeat"] == {"setpoint": 21.5}


@pytest.mark.parametrize(
    ("fahrenheit", "requested", "text"),
    [
        # A Fahrenheit owner's 70 is read as 70 °C, and the error says so.
        (
            True,
            70,
            "70 °C is above the highest setpoint the thermostat allows, 30 °C.",
        ),
        (True, 4.7, "4.7 °C is below the lowest setpoint the thermostat allows, 5 °C."),
        (
            False,
            31,
            "31 °C is above the highest setpoint the thermostat allows, 30 °C.",
        ),
        (
            False,
            4.7,
            "4.7 °C is below the lowest setpoint the thermostat allows, 5 °C.",
        ),
    ],
)
async def test_hold_out_of_range_is_refused_in_celsius(
    hass: HomeAssistant,
    cloud: FakeCloud,
    *,
    fahrenheit: bool,
    requested: float,
    text: str,
) -> None:
    """Services are strict: nothing is sent for an out-of-range hold."""
    use_units(hass, fahrenheit)
    cloud.schedules = [{"id": "existing", "deviceIds": [DEVICE_ID]}]
    await setup_entry(hass, cloud_only())
    with pytest.raises(ServiceValidationError) as err:
        await call(hass, DOMAIN, "hold", entity_id=CLIMATE, temperature=requested)
    assert message(err.value) == text
    assert cloud.hold is None
    assert cloud.updates == []


@pytest.mark.parametrize(
    ("fahrenheit", "temperatures", "expected"),
    [(True, (21.2, 17.75), (21.0, 18.0)), (False, (21.2, 17.75), (21.0, 18.0))],
)
async def test_schedule_takes_celsius_and_rounds(
    hass: HomeAssistant,
    cloud: FakeCloud,
    *,
    fahrenheit: bool,
    temperatures: tuple[float, float],
    expected: tuple[float, float],
) -> None:
    """Schedule events are °C on any unit system (#48), rounded to the step."""
    use_units(hass, fahrenheit)
    await setup_entry(hass, cloud_only())
    events = [
        {"days": ["mon"], "time": "06:30", "temperature": temperatures[0]},
        {"days": ["tue"], "time": "22:00", "temperature": temperatures[1]},
    ]
    await call(hass, DOMAIN, "set_schedule", entity_id=CLIMATE, events=events)
    (schedule,) = cloud.schedules
    setpoints = {
        e["dayOfWeek"]: e["state"]["targetHeat"]["setpoint"] for e in schedule["events"]
    }
    assert (setpoints["1"], setpoints["2"]) == expected


@pytest.mark.parametrize(
    ("fahrenheit", "requested", "text"),
    [
        (True, 70, "70 °C is above the highest setpoint the thermostat allows, 30 °C."),
        (
            False,
            35,
            "35 °C is above the highest setpoint the thermostat allows, 30 °C.",
        ),
    ],
)
async def test_schedule_out_of_range_is_refused(
    hass: HomeAssistant,
    cloud: FakeCloud,
    *,
    fahrenheit: bool,
    requested: float,
    text: str,
) -> None:
    """No schedule is written when any event is out of range."""
    use_units(hass, fahrenheit)
    await setup_entry(hass, cloud_only())
    events = [{"days": ["mon"], "time": "06:30", "temperature": requested}]
    with pytest.raises(ServiceValidationError) as err:
        await call(hass, DOMAIN, "set_schedule", entity_id=CLIMATE, events=events)
    assert message(err.value) == text
    assert cloud.schedules == []


# ---- setpoint limits and offset -------------------------------------------------


@pytest.mark.parametrize(
    ("fahrenheit", "entity_id", "value", "field", "written", "shown"),
    [
        (True, HIGHEST, 75, "lockoutMax", 24.0, "75"),
        (True, LOWEST, 50, "lockoutMin", 10.0, "50"),
        (False, HIGHEST, 24.3, "lockoutMax", 24.5, "24.5"),
        (False, LOWEST, 10, "lockoutMin", 10, "10.0"),
    ],
)
async def test_setpoint_limits_take_the_owners_unit(
    hass: HomeAssistant,
    cloud: FakeCloud,
    *,
    fahrenheit: bool,
    entity_id: str,
    value: float,
    field: str,
    written: float,
    shown: str,
) -> None:
    """Limit numbers show and accept whole °F, or °C, through the rules."""
    use_units(hass, fahrenheit)
    entry = cloud_only()
    await setup_entry(hass, entry)
    state = hass.states.get(entity_id)
    unit = UnitOfTemperature.FAHRENHEIT if fahrenheit else UnitOfTemperature.CELSIUS
    assert state.attributes["unit_of_measurement"] == unit
    assert state.attributes["step"] == (1 if fahrenheit else 0.5)
    assert (state.attributes["min"], state.attributes["max"]) == (
        (41, 86) if fahrenheit else (5, 30)
    )
    await call(hass, "number", "set_value", entity_id=entity_id, value=value)
    assert cloud.updates[-1] == {"source": 3, "targetHeat": {field: written}}
    await entry.runtime_data.cloud.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == shown


@pytest.mark.parametrize(
    ("fahrenheit", "value", "written", "shown", "bounds"),
    [
        (True, 1, 1.0, "1.0", (-5, 5)),
        (True, -1.5, -1.5, "-1.5", (-5, 5)),
        (False, 1.5, 1.5, "1.5", (-5, 5)),
    ],
)
async def test_temperature_offset_is_celsius_whatever_the_unit_system(
    hass: HomeAssistant,
    cloud: FakeCloud,
    *,
    fahrenheit: bool,
    value: float,
    written: float,
    shown: str,
    bounds: tuple[int, int],
) -> None:
    """The offset stays °C with a 0.5 step: an automation's 1 means 1 °C."""
    use_units(hass, fahrenheit)
    entry = cloud_only()
    await setup_entry(hass, entry)
    state = hass.states.get(OFFSET)
    assert state.attributes["unit_of_measurement"] == UnitOfTemperature.CELSIUS
    assert state.attributes["device_class"] == "temperature_delta"
    assert state.attributes["step"] == 0.5
    assert (state.attributes["min"], state.attributes["max"]) == bounds
    await call(hass, "number", "set_value", entity_id=OFFSET, value=value)
    assert cloud.updates[-1] == {"source": 3, "tracking": {"ambientOffset": written}}
    await entry.runtime_data.cloud.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get(OFFSET).state == shown


# ---- the strict fakes themselves --------------------------------------------------


async def test_fakes_refuse_off_step_setpoints(
    hass: HomeAssistant, controller: FakeController, cloud: FakeCloud
) -> None:
    """A write that skipped the rules would fail, as on a real thermostat."""
    await setup_entry(hass, hub_entry())
    pairing = controller.aliases[HKID]
    aid, iid = fake_characteristic_address(
        pairing, CharacteristicsTypes.TEMPERATURE_TARGET
    )
    assert await pairing.put_characteristics([(aid, iid, 22.78)]) == {
        (aid, iid): {"status": -70410}
    }
    # The client itself refuses a target that didn't come from the rules.
    runtime = hass.config_entries.async_entries(DOMAIN)[0].runtime_data
    local = next(iter(runtime.thermostats.values())).local
    with pytest.raises(TypeError, match="Setpoint"):
        await local.start_write({Field.TARGET_TEMPERATURE: 22.5})
    response = await async_get_clientsession(hass).post(
        f"{API_BASE}/state/{DEVICE_ID}/update",
        data=json.dumps({"targetHeat": {"setpoint": 22.78}}),
    )
    assert response.status == 400
    assert cloud.updates == []


# ---- whole-°F taps with a coarse step ---------------------------------------------


def ac_in_cool(cloud: FakeCloud, setpoint: float) -> Any:
    """A cloud-only Mini-Split AC in cool mode with a 1 °C remote step."""
    record = {
        "Model": "AC-V1-0",
        # Key 4 is cool; the remote offers every whole degree from 16 to 30.
        "SupportedModes": {"4": {"temperatures": list(range(16, 31))}},
    }
    cloud.devices["DevicesObj"][DEVICE_ID].update(record)
    cloud.bootstrap["data"]["devices"][DEVICE_ID].update(record)
    report(cloud, "modes", mode=3)
    cloud.state["targetCool"] = {
        "reported": {"setpoint": setpoint},
        "desired": {"setpoint": setpoint},
    }
    return cloud_only()


@pytest.mark.parametrize("fahrenheit", [True, False])
async def test_ac_cool_range_comes_from_its_record(
    hass: HomeAssistant, cloud: FakeCloud, fahrenheit: bool
) -> None:
    """The AC remote's 16-30 °C cool range and 1 °C step bound the card."""
    use_units(hass, fahrenheit)
    await setup_entry(hass, ac_in_cool(cloud, 23))
    state = attrs(hass)
    assert (state["min_temp"], state["max_temp"]) == (
        # 60 °F is 15.6 °C, which the 1 °C step rounds to 16 °C.
        (60, 86) if fahrenheit else (16, 30)
    )
    assert state["target_temp_step"] == 1
    assert state["temperature"] == (73 if fahrenheit else 23)


@pytest.mark.parametrize(
    ("current", "requested", "written"),
    [
        # + from 73 °F asks for 74 °F (23.3 °C), which rounds back to 23 °C.
        (23, 74, 24.0),
        # - from 75 °F asks for 74 °F, which rounds to 23 °C anyway.
        (24, 74, 23.0),
        # - from 73 °F (23 °C) asks for 72 °F (22.2 °C), which is 22 °C.
        (23, 72, 22.0),
        # Re-picking the current label sends nothing.
        (23, 73, None),
        # A typed value far away goes to its nearest setpoint.
        (23, 80, 27.0),
        # A tap past the top is clamped to it.
        (30, 87, 30.0),
    ],
)
async def test_fahrenheit_taps_always_move_with_a_coarse_step(
    hass: HomeAssistant,
    cloud: FakeCloud,
    hass_admin_user: MockUser,
    *,
    current: float,
    requested: int,
    written: float | None,
) -> None:
    """A whole-°F step never rounds back to the setpoint it started from."""
    use_units(hass, True)
    await setup_entry(hass, ac_in_cool(cloud, current))
    if requested > 86:
        with pytest.raises(ServiceValidationError):
            await set_temperature(hass, user=hass_admin_user, temperature=requested)
        assert cloud.updates == []
        return
    await set_temperature(hass, user=hass_admin_user, temperature=requested)
    expected = (
        [] if written is None else [{"source": 3, "targetCool": {"setpoint": written}}]
    )
    assert cloud.updates == expected


@pytest.mark.parametrize(
    ("current", "requested", "written"),
    [
        # 74 °F is 23.3 °C: its nearest setpoint is the current one, re-sent.
        (23, 74, 23.0),
        # From 24 °C the nearest setpoint to 74 °F is still 23 °C.
        (24, 74, 23.0),
        # The label already shown is kept, as for a person.
        (23, 73, None),
        (23, 80, 27.0),
    ],
)
async def test_automation_targets_go_to_the_nearest_setpoint(
    hass: HomeAssistant,
    cloud: FakeCloud,
    *,
    current: float,
    requested: int,
    written: float | None,
) -> None:
    """A call with no person behind it isn't a + or -, so it is never nudged."""
    use_units(hass, True)
    await setup_entry(hass, ac_in_cool(cloud, current))
    await set_temperature(hass, temperature=requested)
    expected = (
        [] if written is None else [{"source": 3, "targetCool": {"setpoint": written}}]
    )
    assert cloud.updates == expected


async def test_automation_repeating_a_target_settles(
    hass: HomeAssistant, cloud: FakeCloud, hass_admin_user: MockUser
) -> None:
    """Re-asserting 74 °F used to flip a 1 °C-step AC between 23 and 24 °C."""
    use_units(hass, True)
    await setup_entry(hass, ac_in_cool(cloud, 24))
    settled = {"source": 3, "targetCool": {"setpoint": 23.0}}
    for _ in range(4):
        await set_temperature(hass, temperature=74)
    assert cloud.updates == [settled] * 4
    # A script or automation started by a person has a parent context.
    child = Context(user_id=hass_admin_user.id, parent_id="01JPARENT")
    await call(
        hass,
        "climate",
        "set_temperature",
        context=child,
        entity_id=CLIMATE,
        temperature=74,
    )
    assert cloud.updates == [settled] * 5


@pytest.mark.parametrize(
    ("requested", "written"),
    # 22.5 °C shows as 73 °F; picking 73 again leaves it, though 73 °F alone
    # would round to 23 °C.
    [(74, 23.5), (72, 22.0), (73, None), (75, 24.0)],
)
async def test_fahrenheit_taps_on_half_degree_heaters_are_unchanged(
    hass: HomeAssistant, cloud: FakeCloud, requested: int, written: float | None
) -> None:
    """With a 0.5 °C step every whole °F has its own setpoint, as before."""
    use_units(hass, True)
    report(cloud, "targetHeat", setpoint=22.5)
    await setup_entry(hass, cloud_only())
    await set_temperature(hass, temperature=requested)
    expected = (
        [] if written is None else [{"source": 3, "targetHeat": {"setpoint": written}}]
    )
    assert cloud.updates == expected


async def test_repicking_the_label_writes_nothing_over_homekit(
    hass: HomeAssistant, controller: FakeController
) -> None:
    """The local path skips the write too (21.5 °C shows as 71 °F)."""
    use_units(hass, True)
    pairing = controller.aliases[HKID]
    await setup_entry(hass, hub_entry(signed_in=False))
    assert attrs(hass)["temperature"] == 71
    with patch.object(
        pairing, "put_characteristics", AsyncMock(wraps=pairing.put_characteristics)
    ) as put:
        await set_temperature(hass, temperature=71)
        put.assert_not_awaited()
        await set_temperature(hass, temperature=72)
        put.assert_awaited_once()


async def test_mode_change_with_the_shown_label_still_writes(
    hass: HomeAssistant, cloud: FakeCloud
) -> None:
    """A mode change in the same call has no current label to keep."""
    use_units(hass, True)
    report(cloud, "targetHeat", setpoint=22.5)
    report(cloud, "modes", mode=0)
    await setup_entry(hass, cloud_only())
    await set_temperature(hass, hvac_mode="heat", temperature=73)
    assert cloud.updates == [
        {"source": 3, "modes": {"mode": 4}},
        {"source": 3, "targetHeat": {"setpoint": 23.0}},
    ]


async def test_central_hvac_mode_change_with_the_shown_label_still_writes(
    hass: HomeAssistant, cloud: FakeCloud
) -> None:
    """Newer models apply the mode and the target in one command."""
    use_units(hass, True)
    sub = configure(cloud, "ST-V1-0")
    cloud.state["targetHeat"]["reported"]["setpoint"] = 22.5
    # Heat/cool mode now; heat's 22.5 °C shows as 73 °F once in heat mode.
    await setup_entry(hass, hub_entry(subentries=[sub]))
    await set_temperature(hass, hvac_mode="heat", temperature=73)
    assert cloud.updates == [
        {"source": 3, "modes": {"mode": 4}, "targetHeat": {"setpoint": 23.0}}
    ]


async def test_central_hvac_pair_taps_move_each_side(
    hass: HomeAssistant, cloud: FakeCloud, hass_admin_user: MockUser
) -> None:
    """The heat/cool pair steps each side with its own (1 °C) step."""
    use_units(hass, True)
    sub = configure(cloud, "ST-V1-0")
    auto = cloud.capabilities["features"]["climateControl"]["auto"]
    auto["heatSetpoint"]["validValues"] = [float(v) for v in range(10, 29)]
    auto["coolSetpoint"]["validValues"] = [float(v) for v in range(18, 36)]
    # Heat 20 °C (68 °F) and cool 24 °C (75 °F), in heat/cool mode.
    await setup_entry(hass, hub_entry(subentries=[sub]))
    assert (attrs(hass)["target_temp_low"], attrs(hass)["target_temp_high"]) == (68, 75)
    # + on the low end asks for 69 °F (20.6 °C), which rounds back to 20 °C.
    await set_temperature(
        hass, user=hass_admin_user, target_temp_low=69, target_temp_high=75
    )
    assert cloud.updates[-1] == {
        "source": 3,
        "targetAuto": {"heatSetpoint": 21.0, "coolSetpoint": 24.0},
    }


@pytest.mark.parametrize(
    ("low", "high", "expected"),
    [
        # Both labels picked again: nothing is sent.
        (68, 75, None),
        # Heat kept at 20 °C; 77 °F is 25 °C.
        (68, 77, (20.0, 25.0)),
        # Cool kept at 24 °C; 66 °F is 18.9 °C, so 19 °C.
        (66, 75, (19.0, 24.0)),
    ],
)
async def test_central_hvac_pair_keeps_each_repicked_side(
    hass: HomeAssistant,
    cloud: FakeCloud,
    low: int,
    high: int,
    expected: tuple[float, float] | None,
) -> None:
    """Each side of a heat/cool pair keeps its value when its label is re-picked."""
    use_units(hass, True)
    sub = configure(cloud, "ST-V1-0")
    # Heat 20 °C (68 °F) and cool 24 °C (75 °F), in heat/cool mode.
    await setup_entry(hass, hub_entry(subentries=[sub]))
    await set_temperature(hass, target_temp_low=low, target_temp_high=high)
    if expected is None:
        assert cloud.updates == []
    else:
        assert cloud.updates == [
            {
                "source": 3,
                "targetAuto": {
                    "heatSetpoint": expected[0],
                    "coolSetpoint": expected[1],
                },
            }
        ]


# ---- review fixes ---------------------------------------------------------------


async def test_messages_round_halfway_up(hass: HomeAssistant, cloud: FakeCloud) -> None:
    """21.25-style values read 30.3, not Python's 30.2."""
    cloud.schedules = [{"id": "existing", "deviceIds": [DEVICE_ID]}]
    await setup_entry(hass, cloud_only())
    with pytest.raises(ServiceValidationError) as err:
        await call(hass, DOMAIN, "hold", entity_id=CLIMATE, temperature=30.25)
    assert message(err.value) == (
        "30.3 °C is above the highest setpoint the thermostat allows, 30 °C."
    )


async def test_hold_in_heat_cool_mode_is_refused_on_a_heater(
    hass: HomeAssistant, cloud: FakeCloud
) -> None:
    """A heat/cool hold on a heater is refused for its mode, before its target."""
    cloud.schedules = [{"id": "existing", "deviceIds": [DEVICE_ID]}]
    await setup_entry(hass, cloud_only())
    with pytest.raises(ServiceValidationError) as err:
        await call(
            hass,
            DOMAIN,
            "hold",
            entity_id=CLIMATE,
            temperature=21,
            hvac_mode="heat_cool",
        )
    assert err.value.translation_key == "mode_not_supported"
    assert message(err.value) == (
        "Living Room doesn't support heat_cool mode, so a hold or schedule event "
        "can't use it. Its modes are: off, heat."
    )
    assert cloud.hold is None


async def test_off_schedule_event_drops_its_temperature(
    hass: HomeAssistant, cloud: FakeCloud
) -> None:
    """An off event carries no setpoint, even one that would be out of range."""
    await setup_entry(hass, cloud_only())
    events = [
        {"days": ["mon"], "time": "06:30", "temperature": 21},
        {"days": ["tue"], "time": "22:00", "temperature": 99, "hvac_mode": "off"},
    ]
    await call(hass, DOMAIN, "set_schedule", entity_id=CLIMATE, events=events)
    (schedule,) = cloud.schedules
    states = {e["dayOfWeek"]: e["state"] for e in schedule["events"]}
    assert states["2"] == {"modes": {"mode": 0}}


async def test_setpoint_limits_must_stay_apart(
    hass: HomeAssistant, cloud: FakeCloud
) -> None:
    """A lowest setpoint at or above the highest is refused by the rules."""
    use_units(hass, True)
    report(cloud, "targetHeat", lockoutMin=18, lockoutMax=21.5)
    await setup_entry(hass, cloud_only())
    with pytest.raises(ServiceValidationError) as err:
        await call(hass, "number", "set_value", entity_id=LOWEST, value=71)
    assert err.value.translation_key == "limits_invalid"
    assert cloud.updates == []


async def test_rules_are_rebuilt_only_when_their_inputs_change(
    hass: HomeAssistant, cloud: FakeCloud
) -> None:
    """One rules object per thermostat, replaced when a limit changes."""
    entry = cloud_only()
    await setup_entry(hass, entry)
    thermostat = next(iter(entry.runtime_data.thermostats.values()))
    rules = thermostat.rules
    assert thermostat.rules is rules
    await call(hass, "number", "set_value", entity_id=HIGHEST, value=25)
    await entry.runtime_data.cloud.async_refresh()
    await hass.async_block_till_done()
    assert thermostat.rules is not rules
    assert thermostat.rules.setpoint_max == 25
