"""MQTT-preferred humidity and numeric heating duty-cycle values."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

from homeassistant.core import HomeAssistant
from mysa_client.cloud import Mode
from mysa_client.homekit import LocalMode
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mysa_local.climate import MysaClimate
from custom_components.mysa_local.sensor import (
    HeatingDutyCycleSensor,
    HumiditySensor,
    async_setup_entry as async_setup_sensor_entry,
)
from custom_components.mysa_local.thermostat import (
    ACTION_IDLE,
    CURRENT_STATE,
    TARGET_STATE,
    Thermostat,
)

from .common import DEVICE_ID


class FakeMqtt:
    """Fresh MQTT values and the notifications used for entity discovery."""

    def __init__(self) -> None:
        """Start with no telemetry."""
        self.values: dict[tuple[str, str], float] = {}
        self.seen: set[tuple[str, str]] = set()
        self.listeners: set[Any] = set()
        self.connected = True

    def value(self, device_id: str, field: str) -> float | None:
        """Return the current value, if fresh."""
        if not self.connected:
            return None
        return self.values.get((device_id, field))

    def has_value(self, device_id: str, field: str) -> bool:
        """Return whether a valid value has ever been received."""
        return (device_id, field) in self.seen

    def async_add_listener(self, listener: Any) -> Any:
        """Subscribe to new values."""
        self.listeners.add(listener)
        return lambda: self.listeners.discard(listener)

    def set_value(self, device_id: str, field: str, value: float) -> None:
        """Publish a test value and notify subscribers."""
        self.values[(device_id, field)] = value
        self.seen.add((device_id, field))
        for listener in list(self.listeners):
            listener()


class FakeCloud:
    """Freshness-aware cloud readings without network activity."""

    def __init__(self, **values: Any) -> None:
        """Set up one set of test measurements."""
        self.last_update_success = True
        self.update_interval = timedelta(seconds=60)
        self._state = SimpleNamespace(
            connected=True,
            reading_time=datetime.now(UTC),
            model="BB-V2-0-L",
            firmware=None,
            mode=Mode.HEAT,
            room_temperature=21.5,
            humidity=values.get("humidity"),
            duty_cycle=values.get("duty_cycle"),
        )
        self.fresh = {name for name, value in values.items() if value is not None}

    def state(self, device_id: str) -> Any:
        """Return the one test device's current cloud state."""
        assert device_id == DEVICE_ID
        return self._state

    def record(self, device_id: str) -> None:
        """The test device has no account record beyond its model identity."""
        assert device_id == DEVICE_ID

    def measurement_fresh(self, device_id: str, name: str) -> bool:
        """Return whether the requested measurement is fresh."""
        assert device_id == DEVICE_ID
        return name in self.fresh

    def measurement_max_age(self, device_id: str) -> timedelta:
        """Use a short age for the live-state check."""
        assert device_id == DEVICE_ID
        return timedelta(minutes=10)


def _local(*, mode: LocalMode = LocalMode.HEAT, action: str = ACTION_IDLE) -> Any:
    """A connected HomeKit Lite with a chosen relay state."""
    return SimpleNamespace(
        available=True,
        ready=True,
        values={TARGET_STATE: mode, CURRENT_STATE: action},
        info=SimpleNamespace(model="BB-V2-0-L", firmware=None, serial=None),
        add_listener=lambda _listener: None,
    )


def _thermostat(
    hass: HomeAssistant,
    *,
    mqtt: FakeMqtt | None = None,
    cloud: FakeCloud | None = None,
    local: Any | None = None,
) -> Thermostat:
    """Build one thermostat with optional fake transports."""
    subentry = SimpleNamespace(
        subentry_id="subentry",
        data={
            "device_id": DEVICE_ID,
            "model": "BB-V2-0-L",
            "heater_watts": 1500,
        },
        title="Living Room",
    )
    return Thermostat(
        hass,
        subentry,
        local,
        lambda: cloud,
        mqtt_getter=lambda: mqtt,
    )


def test_mqtt_humidity_wins_without_a_healthy_cloud_poll(
    hass: HomeAssistant,
) -> None:
    """A fresh MQTT humidity reading remains usable during REST failure."""
    mqtt = FakeMqtt()
    mqtt.set_value(DEVICE_ID, "humidity", 43)
    cloud = FakeCloud(humidity=39)
    cloud.last_update_success = False
    thermostat = _thermostat(hass, mqtt=mqtt, cloud=cloud)

    sensor = HumiditySensor(thermostat)
    climate = MysaClimate(thermostat)
    assert thermostat.humidity == 43
    assert thermostat.humidity_source == "mqtt"
    assert sensor.available
    assert sensor.native_value == 43
    assert sensor.extra_state_attributes["source"] == "mqtt"
    assert climate.current_humidity == 43
    mqtt.connected = False
    assert climate.current_humidity is None


def test_invalid_or_stale_mqtt_humidity_falls_back_to_cloud(
    hass: HomeAssistant,
) -> None:
    """The cloud value is used when the MQTT candidate fails validation."""
    mqtt = FakeMqtt()
    mqtt.set_value(DEVICE_ID, "humidity", 101)
    cloud = FakeCloud(humidity=39)
    thermostat = _thermostat(hass, mqtt=mqtt, cloud=cloud)

    assert thermostat.humidity == 39
    assert thermostat.humidity_source == "cloud"
    mqtt.connected = False
    assert thermostat.humidity == 39


def test_mqtt_duty_cycle_wins_over_local_relay_boolean(
    hass: HomeAssistant,
) -> None:
    """Use the numeric MQTT fraction while preserving local relay action."""
    mqtt = FakeMqtt()
    mqtt.set_value(DEVICE_ID, "duty_cycle", 0.37)
    cloud = FakeCloud(duty_cycle=0.21)
    thermostat = _thermostat(hass, mqtt=mqtt, cloud=cloud, local=_local())

    sensor = HeatingDutyCycleSensor(thermostat)
    assert thermostat.duty_cycle == pytest.approx(0.37)
    assert thermostat.duty_cycle_source == "mqtt"
    assert thermostat.heat_fraction == pytest.approx(0.37)
    assert thermostat.action == ACTION_IDLE
    assert sensor.available
    assert sensor.native_value == pytest.approx(37)
    assert sensor.extra_state_attributes["source"] == "mqtt"

    mqtt.connected = False
    assert thermostat.duty_cycle == pytest.approx(0.21)
    assert thermostat.heat_fraction == 0
    assert thermostat.action == ACTION_IDLE


def test_local_relay_boolean_is_not_exposed_as_numeric_duty_cycle(
    hass: HomeAssistant,
) -> None:
    """No numeric sensor value is invented from a local on/off relay state."""
    thermostat = _thermostat(hass, local=_local(action=ACTION_IDLE))

    assert thermostat.heat_fraction == 0
    assert thermostat.duty_cycle is None
    assert HeatingDutyCycleSensor(thermostat).native_value is None


async def test_mqtt_readings_discover_humidity_and_duty_sensors(
    hass: HomeAssistant,
) -> None:
    """MQTT telemetry can create sensors after initial platform setup."""
    mqtt = FakeMqtt()
    thermostat = _thermostat(hass, mqtt=mqtt)
    entry = MockConfigEntry(domain="mysa_local", title="Mysa", data={})
    entry.add_to_hass(hass)
    entry.runtime_data = SimpleNamespace(
        thermostats={thermostat.subentry_id: thermostat},
        cloud=None,
        mqtt=mqtt,
    )
    added: list[Any] = []

    def add_entities(entities: list[Any], *, config_subentry_id: str) -> None:
        assert config_subentry_id == thermostat.subentry_id
        added.extend(entities)

    await async_setup_sensor_entry(hass, entry, add_entities)
    assert not any(isinstance(entity, HumiditySensor) for entity in added)
    assert not any(isinstance(entity, HeatingDutyCycleSensor) for entity in added)

    mqtt.set_value(DEVICE_ID, "humidity", 44)
    mqtt.set_value(DEVICE_ID, "duty_cycle", 0.42)

    humidity = next(entity for entity in added if isinstance(entity, HumiditySensor))
    duty = next(
        entity for entity in added if isinstance(entity, HeatingDutyCycleSensor)
    )
    assert humidity.native_value == 44
    assert duty.native_value == pytest.approx(42)
