"""Failed HomeKit characteristic statuses must not count as a healthy path."""

from unittest.mock import AsyncMock, patch

from custom_components.mysa_local.const import CONNECTION_CLOUD
from custom_components.mysa_local.local import (
    CURRENT_STATE,
    CURRENT_TEMPERATURE,
    DISPLAY_UNITS,
    TARGET_STATE,
    TARGET_TEMPERATURE,
)

from .common import HKID, hub_entry
from .conftest import setup_entry

ESSENTIAL_CHARS = (
    CURRENT_TEMPERATURE,
    TARGET_TEMPERATURE,
    CURRENT_STATE,
    TARGET_STATE,
)


def _characteristic_results(local, *, failed=()):
    """Build a successful response, with optional per-characteristic failures."""
    values = {
        CURRENT_TEMPERATURE: 21.5,
        TARGET_TEMPERATURE: 20.0,
        CURRENT_STATE: 0,
        TARGET_STATE: 1,
        DISPLAY_UNITS: 0,
    }
    result = {}
    for ctype, meta in local.chars.items():
        key = (meta.aid, meta.iid)
        if ctype in failed:
            result[key] = {"status": -70402, "description": "Unable to communicate"}
        elif ctype in values:
            result[key] = {"value": values[ctype]}
    return result


async def _setup_thermostat(hass):
    """Load the paired thermostat and return its local and cloud objects."""
    await setup_entry(hass, hub_entry())
    entry = hass.config_entries.async_entries("mysa_local")[0]
    thermostat = next(iter(entry.runtime_data.thermostats.values()))
    return thermostat.local, thermostat


async def test_all_essential_status_errors_mark_local_unavailable_and_fallback(
    hass, controller, cloud
):
    """An all-error read must expose the cloud path and route writes through it."""
    local, thermostat = await _setup_thermostat(hass)
    pairing = controller.aliases[HKID]
    result = _characteristic_results(local, failed=ESSENTIAL_CHARS)

    with patch.object(
        pairing, "get_characteristics", new=AsyncMock(return_value=result)
    ):
        await local.async_poll()

    assert not local.available
    assert thermostat.connection_path == CONNECTION_CLOUD
    await thermostat.async_set_temperature(22.5)
    assert cloud.updates == [{"source": 3, "targetHeat": {"setpoint": 22.5}}]


async def test_partial_essential_status_error_marks_local_unavailable(
    hass, controller, cloud
):
    """Successful sibling values cannot hide a failed required characteristic."""
    local, _thermostat = await _setup_thermostat(hass)
    pairing = controller.aliases[HKID]
    before = dict(local.values)
    result = _characteristic_results(local, failed=(TARGET_TEMPERATURE,))

    with patch.object(
        pairing, "get_characteristics", new=AsyncMock(return_value=result)
    ):
        await local.async_poll()

    assert not local.available
    assert local.values == before
    assert str(TARGET_TEMPERATURE) in (local.last_error or "")


async def test_optional_characteristic_error_does_not_fail_essential_read(
    hass, controller, cloud
):
    """A display-unit error is tolerated when the climate values were read."""
    local, _thermostat = await _setup_thermostat(hass)
    pairing = controller.aliases[HKID]
    result = _characteristic_results(local, failed=(DISPLAY_UNITS,))

    with patch.object(
        pairing, "get_characteristics", new=AsyncMock(return_value=result)
    ):
        await local.async_poll()

    assert local.available
    assert local.values[CURRENT_TEMPERATURE] == 21.5


async def test_error_event_does_not_update_value_or_restore_health(
    hass, controller, cloud
):
    """A status-error notification is not a value or a reconnect signal."""
    local, _thermostat = await _setup_thermostat(hass)
    meta = local.chars[CURRENT_TEMPERATURE]
    key = (meta.aid, meta.iid)
    original = local.values[CURRENT_TEMPERATURE]

    local._handle_events({key: {"value": 99.0, "status": -70402}})

    assert not local.available
    assert local.values[CURRENT_TEMPERATURE] == original
    assert (
        local.pairing.accessories.aid(meta.aid).characteristics.iid(meta.iid).value
        == original
    )

    display = local.chars[DISPLAY_UNITS]
    local._handle_events({(display.aid, display.iid): {"value": 1, "status": 0}})
    assert not local.available

    local._handle_events({key: {"status": -70402}})
    assert not local.available

    target_state = local.chars[TARGET_STATE]
    local._handle_events(
        {
            (target_state.aid, target_state.iid): {"value": 0, "status": 0},
        }
    )
    assert not local.available

    # Even a successful event for the failed characteristic cannot prove that
    # the complete essential read set is healthy again.
    local._handle_events({key: {"value": 22.0, "status": 0}})
    assert not local.available
    assert local.values[CURRENT_TEMPERATURE] == 22.0

    # A complete successful poll clears the failed-characteristic latch.
    pairing = controller.aliases[HKID]
    with patch.object(
        pairing,
        "get_characteristics",
        new=AsyncMock(return_value=_characteristic_results(local)),
    ):
        await local.async_poll()

    assert local.available


async def test_optional_value_event_notifies_while_local_path_is_healthy(
    hass, controller, cloud
):
    """Optional values update entities even though they cannot establish health."""
    local, _thermostat = await _setup_thermostat(hass)
    notifications = []
    local.add_listener(lambda: notifications.append(True))
    meta = local.chars[DISPLAY_UNITS]

    local._handle_events({(meta.aid, meta.iid): {"value": 1, "status": 0}})

    assert local.available
    assert local.values[DISPLAY_UNITS] == 1
    assert notifications == [True]


async def test_missing_essential_read_is_not_treated_as_success(
    hass, controller, cloud
):
    """A partial response missing one requested essential value marks down."""
    local, _thermostat = await _setup_thermostat(hass)
    pairing = controller.aliases[HKID]
    result = _characteristic_results(local)
    target = local.chars[TARGET_STATE]
    result.pop((target.aid, target.iid))

    with patch.object(
        pairing, "get_characteristics", new=AsyncMock(return_value=result)
    ):
        await local.async_poll()

    assert not local.available
    assert str(TARGET_STATE) in (local.last_error or "")
