"""Cloud telemetry freshness, relay runtime and fallback heater power."""

from __future__ import annotations

from datetime import timedelta
from typing import Any, cast

from aiohomekit.testing import FakeController
from freezegun.api import FrozenDateTimeFactory
from homeassistant.components.number import DOMAIN as NUMBER_DOMAIN, SERVICE_SET_VALUE
from homeassistant.components.select import (
    DOMAIN as SELECT_DOMAIN,
    SERVICE_SELECT_OPTION,
)
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.mysa_local.const import CONF_CLOUD_RUNTIME, DOMAIN
from custom_components.mysa_local.local import CURRENT_STATE

from .common import HKID, FakeCloud, hub_entry, thermostat_subentry
from .conftest import setup_entry


def _thermostat(hass: HomeAssistant) -> Any:
    """Return the single configured thermostat."""
    runtime = hass.config_entries.async_entries(DOMAIN)[0].runtime_data
    return next(iter(runtime.thermostats.values()))


def _reading(cloud: FakeCloud) -> dict[str, Any]:
    """Return the fake cloud's latest reading payload."""
    return cast(dict[str, Any], cloud.state["latestTelemetry"]["reading"])


def _set_cloud_usage(cloud: FakeCloud, seconds: float) -> None:
    """Set the fake cloud's measured runtime for today's local date."""
    midnight = dt_util.start_of_local_day()
    cloud.usage[0]["T"] = int(midnight.timestamp())
    cloud.usage[0]["summary"]["T"] = int(midnight.timestamp())
    cloud.usage[0]["summary"]["a"]["oH1"] = seconds


@pytest.mark.usefixtures("controller")
@pytest.mark.parametrize(
    "scenario",
    [
        pytest.param((timedelta(minutes=11), True, False), id="stale"),
        pytest.param((None, True, False), id="missing-timestamp"),
        pytest.param((-timedelta(minutes=2), True, False), id="future-timestamp"),
        pytest.param((timedelta(), None, False), id="unknown-connectivity"),
        pytest.param((timedelta(), False, False), id="offline"),
        pytest.param((timedelta(minutes=5), True, True), id="fresh-online"),
    ],
)
async def test_cloud_fallback_requires_fresh_online_telemetry(
    hass: HomeAssistant,
    cloud: FakeCloud,
    freezer: FrozenDateTimeFactory,
    scenario: tuple[timedelta | None, bool | None, bool],
) -> None:
    """Stale, malformed and offline cloud readings cannot serve as fallback."""
    age, connected, expected = scenario
    freezer.move_to("2026-10-11T12:00:00-07:00")
    cloud.auto_telemetry = False
    reading = _reading(cloud)
    if age is None:
        reading.pop("timestamp", None)
    else:
        reading["timestamp"] = (dt_util.utcnow() - age).timestamp()
    telemetry = cloud.state["latestTelemetry"]
    if connected is None:
        telemetry.pop("isConnected", None)
    else:
        telemetry["isConnected"] = connected

    await setup_entry(hass, hub_entry())

    assert _thermostat(hass).cloud_ok is expected


@pytest.mark.usefixtures("controller")
async def test_cloud_fallback_recovers_after_fresh_reading(
    hass: HomeAssistant,
    cloud: FakeCloud,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A subsequent fresh cloud poll restores fallback after stale telemetry."""
    freezer.move_to("2026-10-11T12:00:00-07:00")
    cloud.auto_telemetry = False
    reading = _reading(cloud)
    reading["timestamp"] = (dt_util.utcnow() - timedelta(minutes=11)).timestamp()
    await setup_entry(hass, hub_entry())
    thermostat = _thermostat(hass)
    assert not thermostat.cloud_ok

    reading["timestamp"] = dt_util.utcnow().timestamp()
    await thermostat.cloud.async_refresh()

    assert thermostat.cloud_ok


async def test_successful_refresh_does_not_make_stale_cloud_data_available(
    hass: HomeAssistant,
    cloud: FakeCloud,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A successful HTTP refresh cannot renew an old telemetry timestamp."""
    freezer.move_to("2026-10-11T12:00:00-07:00")
    cloud.auto_telemetry = False
    _set_cloud_usage(cloud, 240)
    reading = _reading(cloud)
    reading["timestamp"] = dt_util.utcnow().timestamp()
    await setup_entry(
        hass, hub_entry(subentries=[thermostat_subentry(pairing=None, watts=1500)])
    )
    thermostat = _thermostat(hass)
    climate = hass.states.get("climate.living_room")
    assert thermostat.cloud_ok
    assert climate is not None
    assert climate.state != "unavailable"
    assert thermostat.runtime_today == pytest.approx(240)

    freezer.tick(timedelta(minutes=11))
    await thermostat.cloud.async_refresh()

    assert thermostat.cloud.last_update_success is True
    assert thermostat.cloud_state.reading_time == dt_util.utcnow() - timedelta(
        minutes=11
    )
    assert thermostat.cloud_ok is False
    assert thermostat.available is False
    climate = hass.states.get("climate.living_room")
    assert climate is not None
    assert climate.state == "unavailable"
    assert thermostat.runtime_today == pytest.approx(240)
    runtime = hass.states.get("sensor.living_room_heating_runtime_today")
    assert runtime is not None
    assert float(runtime.state) == pytest.approx(240 / 3600, abs=0.001)


@pytest.mark.usefixtures("controller")
async def test_stale_telemetry_keeps_connected_cloud_settings_available(
    hass: HomeAssistant,
    controller: FakeController,
    cloud: FakeCloud,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Stale readings block fallback but do not hide connected cloud controls."""
    freezer.move_to("2026-10-11T12:00:00-07:00")
    cloud.auto_telemetry = False
    _reading(cloud)["timestamp"] = (
        dt_util.utcnow() - timedelta(minutes=11)
    ).timestamp()
    await setup_entry(hass, hub_entry())

    thermostat = _thermostat(hass)
    assert thermostat.cloud_connected
    assert not thermostat.cloud_ok

    controller.aliases[HKID].available = False
    await thermostat.local.async_poll()
    await hass.async_block_till_done()

    assert not thermostat.local_ok
    assert not thermostat.available
    climate = hass.states.get("climate.living_room")
    assert climate is not None
    assert climate.state == "unavailable"
    assert hass.states.get("binary_sensor.living_room_cloud_connection").state == "on"
    assert hass.states.get("number.living_room_wake_brightness").state == "100"
    assert hass.states.get("sensor.living_room_humidity").state == "unavailable"
    assert hass.states.get("select.living_room_display_units").state == "celsius"

    await hass.services.async_call(
        NUMBER_DOMAIN,
        SERVICE_SET_VALUE,
        {
            ATTR_ENTITY_ID: "number.living_room_sleep_brightness",
            "value": 40,
        },
        blocking=True,
    )
    assert hass.states.get("number.living_room_sleep_brightness").state == "40"
    assert cloud.updates[-1] == {
        "source": 3,
        "physicalInterface": {"activeIntensity": 100, "idleIntensity": 40},
    }

    # Cloud writes are spaced apart; advance frozen monotonic time before the
    # second setting command.
    freezer.tick(timedelta(seconds=1))
    await hass.services.async_call(
        SELECT_DOMAIN,
        SERVICE_SELECT_OPTION,
        {
            ATTR_ENTITY_ID: "select.living_room_display_units",
            "option": "fahrenheit",
        },
        blocking=True,
    )
    assert hass.states.get("select.living_room_display_units").state == "fahrenheit"
    assert cloud.settings[-1] == {"Format": "fahrenheit"}


@pytest.mark.usefixtures("controller")
@pytest.mark.parametrize(
    "connected",
    [pytest.param(False, id="offline"), pytest.param(None, id="unknown")],
)
async def test_cloud_settings_stay_unavailable_when_device_is_offline_or_unknown(
    hass: HomeAssistant,
    cloud: FakeCloud,
    freezer: FrozenDateTimeFactory,
    connected: bool | None,
) -> None:
    """A successful cloud request does not make controls available if device status is not online."""
    freezer.move_to("2026-10-11T12:00:00-07:00")
    cloud.auto_telemetry = False
    telemetry = cloud.state["latestTelemetry"]
    telemetry["reading"]["timestamp"] = dt_util.utcnow().timestamp()
    if connected is None:
        telemetry.pop("isConnected", None)
    else:
        telemetry["isConnected"] = connected

    await setup_entry(hass, hub_entry())

    thermostat = _thermostat(hass)
    assert not thermostat.cloud_connected
    number = hass.states.get("number.living_room_wake_brightness")
    assert number is not None
    assert number.state == "unavailable"


@pytest.mark.usefixtures("controller")
async def test_fractional_cloud_duty_does_not_inflate_runtime(
    hass: HomeAssistant,
    controller: FakeController,
    cloud: FakeCloud,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Fallback uses measured cloud runtime, not fractional duty as full-on."""
    freezer.move_to("2026-10-11T12:00:00-07:00")
    _set_cloud_usage(cloud, 240)
    _reading(cloud)["dutyCycle"] = 0.4
    controller.aliases[HKID].available = False
    subentry = thermostat_subentry(watts=1500)
    await setup_entry(hass, hub_entry(subentries=[subentry]))

    thermostat = _thermostat(hass)
    assert thermostat.cloud_ok
    assert thermostat.runtime_today == pytest.approx(240)
    # 40% of the rating is the mean load over the cloud reading interval.
    power = hass.states.get("sensor.living_room_heater_power")
    assert power is not None
    assert power.state == "600.0"

    freezer.tick(timedelta(minutes=10))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    # Ten minutes of 40% duty is four minutes, matching the measured cloud
    # total. It must not be treated as ten minutes of continuous relay time.
    assert thermostat.runtime_today == pytest.approx(240)
    assert thermostat.runtime_source == "cloud"
    runtime = hass.states.get("sensor.living_room_heating_runtime_today")
    assert runtime is not None
    assert float(runtime.state) == pytest.approx(240 / 3600, abs=0.001)


@pytest.mark.parametrize(
    "duty_cycle",
    [
        pytest.param(-0.1, id="negative"),
        pytest.param(1.1, id="above-one"),
        pytest.param(float("nan"), id="nan"),
        pytest.param(float("inf"), id="infinity"),
        pytest.param(None, id="missing"),
    ],
)
async def test_invalid_cloud_duty_cycle_has_unknown_power(
    hass: HomeAssistant,
    cloud: FakeCloud,
    freezer: FrozenDateTimeFactory,
    duty_cycle: float | None,
) -> None:
    """Invalid or missing duty fractions do not create misleading power."""
    freezer.move_to("2026-10-11T12:00:00-07:00")
    _reading(cloud)["dutyCycle"] = duty_cycle
    await setup_entry(
        hass, hub_entry(subentries=[thermostat_subentry(pairing=None, watts=1500)])
    )

    assert _thermostat(hass).cloud_ok
    power = hass.states.get("sensor.living_room_heater_power")
    assert power is not None
    assert power.state == "unknown"


@pytest.mark.usefixtures("controller")
async def test_local_relay_runtime_stops_and_resumes_across_fallback(
    hass: HomeAssistant,
    controller: FakeController,
    cloud: FakeCloud,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Only observed local relay time is integrated across connection changes."""
    freezer.move_to("2026-10-11T12:00:00-07:00")
    subentry = thermostat_subentry(watts=1500)
    subentry["data"][CONF_CLOUD_RUNTIME] = False
    await setup_entry(hass, hub_entry(subentries=[subentry]))
    thermostat = _thermostat(hass)
    assert thermostat.local is not None

    thermostat.local.values[CURRENT_STATE] = 1
    thermostat.notify()
    freezer.tick(timedelta(minutes=2))
    assert thermostat.runtime_today == pytest.approx(120)

    # On loss of HomeKit the relay's current state is unknown. A fresh 40%
    # cloud duty reading must not extend the locally observed runtime.
    pairing = controller.aliases[HKID]
    pairing.available = False
    await thermostat.local.async_poll()
    await hass.async_block_till_done()
    freezer.tick(timedelta(minutes=10))
    thermostat.notify()
    assert thermostat.runtime_today == pytest.approx(120)

    # The next local relay observation starts a new interval after recovery.
    pairing.available = True
    pairing.testing.update_aid_iid([(1, 259, 1)])
    await thermostat.local.async_poll()
    await hass.async_block_till_done()
    freezer.tick(timedelta(minutes=2))
    assert thermostat.runtime_today == pytest.approx(240)
