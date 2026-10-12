"""Failed HomeKit characteristic reads must not count as a healthy path."""

from unittest.mock import AsyncMock, patch

from aiohomekit.model.characteristics import CharacteristicsTypes

from custom_components.mysa_local.const import CONNECTION_CLOUD
from custom_components.mysa_local.local_adapter import (
    CURRENT_STATE,
    CURRENT_TEMPERATURE,
    DISPLAY_UNITS,
    TARGET_STATE,
    TARGET_TEMPERATURE,
)

from .common import (
    HKID,
    fake_characteristic_address,
    hub_entry,
    update_fake_characteristic,
)
from .conftest import setup_entry

ESSENTIAL_FIELDS = (
    CURRENT_TEMPERATURE,
    TARGET_TEMPERATURE,
    CURRENT_STATE,
    TARGET_STATE,
)
FIELD_TYPES = {
    CURRENT_TEMPERATURE: CharacteristicsTypes.TEMPERATURE_CURRENT,
    TARGET_TEMPERATURE: CharacteristicsTypes.TEMPERATURE_TARGET,
    CURRENT_STATE: CharacteristicsTypes.HEATING_COOLING_CURRENT,
    TARGET_STATE: CharacteristicsTypes.HEATING_COOLING_TARGET,
    DISPLAY_UNITS: CharacteristicsTypes.TEMPERATURE_UNITS,
}
FIELD_VALUES = {
    CURRENT_TEMPERATURE: 21.5,
    TARGET_TEMPERATURE: 20.0,
    CURRENT_STATE: 0,
    TARGET_STATE: 1,
    DISPLAY_UNITS: 0,
}


def _characteristic_results(pairing, *, failed=(), omitted=()):
    """Build a raw HAP read result for the fake accessory's current fields."""
    result = {}
    for field, value in FIELD_VALUES.items():
        address = fake_characteristic_address(pairing, FIELD_TYPES[field])
        if field in omitted:
            continue
        if field in failed:
            result[address] = {
                "status": -70402,
                "description": "Unable to communicate",
            }
        else:
            result[address] = {"value": value}
    return result


async def _setup_thermostat(hass):
    """Load the paired thermostat and return its local and cloud objects."""
    await setup_entry(hass, hub_entry())
    entry = hass.config_entries.async_entries("mysa_local")[0]
    thermostat = next(iter(entry.runtime_data.thermostats.values()))
    return thermostat.local, thermostat


async def test_all_essential_read_errors_mark_local_unavailable_and_fallback(
    hass, controller, cloud
):
    """An all-error read exposes the cloud path and routes writes through it."""
    local, thermostat = await _setup_thermostat(hass)
    pairing = controller.aliases[HKID]
    result = _characteristic_results(pairing, failed=ESSENTIAL_FIELDS)

    with patch.object(
        pairing, "get_characteristics", new=AsyncMock(return_value=result)
    ):
        await local.async_poll()

    assert not local.available
    assert thermostat.connection_path == CONNECTION_CLOUD
    await thermostat.async_set_temperature(22.5)
    assert cloud.updates == [{"source": 3, "targetHeat": {"setpoint": 22.5}}]


async def test_partial_essential_read_error_marks_local_unavailable(
    hass, controller, cloud
):
    """Successful sibling fields cannot hide a failed required characteristic."""
    local, _thermostat = await _setup_thermostat(hass)
    pairing = controller.aliases[HKID]
    before = dict(local.values)
    result = _characteristic_results(pairing, failed=(TARGET_TEMPERATURE,))

    with patch.object(
        pairing, "get_characteristics", new=AsyncMock(return_value=result)
    ):
        await local.async_poll()

    assert not local.available
    assert local.values == before
    assert TARGET_TEMPERATURE.value in (local.last_error or "")


async def test_optional_characteristic_error_does_not_fail_essential_read(
    hass, controller, cloud
):
    """A display-unit error is tolerated when required climate fields were read."""
    local, _thermostat = await _setup_thermostat(hass)
    pairing = controller.aliases[HKID]
    result = _characteristic_results(pairing, failed=(DISPLAY_UNITS,))

    with patch.object(
        pairing, "get_characteristics", new=AsyncMock(return_value=result)
    ):
        await local.async_poll()

    assert local.available
    assert local.values[CURRENT_TEMPERATURE] == 21.5


async def test_error_event_preserves_values_and_recovers_field_health_independently(
    hass, controller, cloud
):
    """A status-error event cannot update a value or prove a healthy connection."""
    local, _thermostat = await _setup_thermostat(hass)
    pairing = controller.aliases[HKID]
    current_temperature = fake_characteristic_address(
        pairing, FIELD_TYPES[CURRENT_TEMPERATURE]
    )
    original = local.values[CURRENT_TEMPERATURE]

    pairing.testing.set_aid_iid_status([(*current_temperature, -70402)])

    assert not local.available
    assert local.values[CURRENT_TEMPERATURE] == original
    aid, iid = current_temperature
    assert pairing.accessories.aid(aid).characteristics.iid(iid).value == original

    update_fake_characteristic(
        pairing, FIELD_TYPES[DISPLAY_UNITS], FIELD_VALUES[DISPLAY_UNITS] + 1
    )
    assert not local.available

    pairing.testing.set_aid_iid_status([(*current_temperature, -70402)])
    assert not local.available

    update_fake_characteristic(
        pairing, FIELD_TYPES[TARGET_STATE], FIELD_VALUES[TARGET_STATE] - 1
    )
    assert not local.available

    # A successful event clears only its own field's failure. The unrelated
    # target-mode failure keeps the local path unavailable.
    target_mode = fake_characteristic_address(pairing, FIELD_TYPES[TARGET_STATE])
    pairing.testing.set_aid_iid_status(
        [(*current_temperature, -70402), (*target_mode, -70402)]
    )
    assert not local.available

    update_fake_characteristic(pairing, FIELD_TYPES[CURRENT_TEMPERATURE], 22.0)
    assert not local.available
    assert local.values[CURRENT_TEMPERATURE] == 22.0

    update_fake_characteristic(pairing, FIELD_TYPES[TARGET_STATE], 1)
    assert local.available

    # A complete successful poll also recovers after a later field failure.
    pairing.testing.set_aid_iid_status([(*current_temperature, -70402)])
    assert not local.available
    with patch.object(
        pairing,
        "get_characteristics",
        new=AsyncMock(return_value=_characteristic_results(pairing)),
    ):
        await local.async_poll()

    assert local.available


async def test_optional_value_event_notifies_while_local_path_is_healthy(
    hass, controller, cloud
):
    """Optional values still notify entities while the local path is healthy."""
    local, _thermostat = await _setup_thermostat(hass)
    pairing = controller.aliases[HKID]
    notifications = []
    local.add_listener(lambda: notifications.append(True))

    update_fake_characteristic(pairing, FIELD_TYPES[DISPLAY_UNITS], 1)

    assert local.available
    assert local.values[DISPLAY_UNITS] == "F"
    assert notifications == [True]


async def test_missing_essential_read_is_not_treated_as_success(
    hass, controller, cloud
):
    """A response missing one essential field marks the local path down."""
    local, _thermostat = await _setup_thermostat(hass)
    pairing = controller.aliases[HKID]
    result = _characteristic_results(pairing, omitted=(TARGET_STATE,))

    with patch.object(
        pairing, "get_characteristics", new=AsyncMock(return_value=result)
    ):
        await local.async_poll()

    assert not local.available
    assert TARGET_STATE.value in (local.last_error or "")
