"""Sensors: climate readings, connection diagnostics, runtime, power and energy."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from homeassistant.components.sensor import (
    RestoreSensor,
    SensorDeviceClass,
    SensorEntity,
    SensorExtraStoredData,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    EntityCategory,
    UnitOfEnergy,
    UnitOfPower,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.restore_state import ExtraStoredData

from .account import ACCOUNT_OFF, ACCOUNT_ON
from .const import CONNECTION_CLOUD, CONNECTION_LOCAL, CONNECTION_OFFLINE
from .data import MysaConfigEntry
from .entity import MysaCloudEntity, MysaEntity, async_add_when_ready
from .thermostat import Thermostat

PARALLEL_UPDATES = 0
RUNTIME_REFRESH = timedelta(minutes=1)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MysaConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the sensors of every thermostat."""

    def _entities(thermostat: Thermostat) -> list[MysaEntity]:
        entities: list[MysaEntity] = [
            TemperatureSensor(thermostat),
            ConnectionPathSensor(thermostat),
            HomeKitPairingSensor(thermostat),
            HeatingRuntimeSensor(thermostat),
        ]
        if thermostat.cloud is not None:
            entities.append(MysaAccountSensor(thermostat))
        if thermostat.heater_watts > 0:
            entities.append(HeaterPowerSensor(thermostat))
            entities.append(EnergySensor(thermostat))
        state = thermostat.cloud_state
        if state is not None and state.humidity is not None:
            entities.append(HumiditySensor(thermostat))
        if state is not None and state.rssi is not None:
            entities.append(WifiSignalSensor(thermostat))
        return entities

    async_add_when_ready(entry, async_add_entities, _entities)


class TemperatureSensor(MysaEntity, SensorEntity):
    """Room temperature, for history and statistics."""

    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 1

    def __init__(self, thermostat: Thermostat) -> None:
        """Create the entity."""
        super().__init__(thermostat, "temperature")

    @property
    def native_value(self) -> float | None:
        """Room temperature in °C."""
        return self.thermostat.current_temperature


class HumiditySensor(MysaCloudEntity, SensorEntity):
    """Room humidity, as the thermostat reports it to the cloud.

    Created whenever the cloud reports a value, whatever the capability data
    says: the Baseboard Lite's capabilities claim no humidity sensor, yet it
    reports a temperature-compensated reading that matched a separate sensor in
    the same room (dew point within 1 °C, 2026-10-11).
    """

    _attr_device_class = SensorDeviceClass.HUMIDITY
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 0

    def __init__(self, thermostat: Thermostat) -> None:
        """Create the entity."""
        super().__init__(thermostat, "humidity")

    @property
    def available(self) -> bool:
        """Only expose humidity while its telemetry reading is fresh."""
        return self.thermostat.cloud_ok

    @property
    def native_value(self) -> float | None:
        """Relative humidity in %."""
        return self.thermostat.humidity


class ConnectionPathSensor(MysaEntity, SensorEntity):
    """Which path is serving the thermostat: local, cloud or offline."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_options = [CONNECTION_LOCAL, CONNECTION_CLOUD, CONNECTION_OFFLINE]
    _attr_translation_key = "connection_path"

    def __init__(self, thermostat: Thermostat) -> None:
        """Create the entity."""
        super().__init__(thermostat, "connection_path")

    @property
    def available(self) -> bool:
        """Always available: 'offline' is a valid answer."""
        return True

    @property
    def native_value(self) -> str:
        """local, cloud or offline."""
        return self.thermostat.connection_path

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Why the local path is down, if it is."""
        local = self.thermostat.local
        if local is None:
            return {"homekit": "not paired"}
        attrs: dict[str, Any] = {"homekit_host": local.host}
        if not local.available and local.last_error:
            attrs["homekit_error"] = local.last_error
        return attrs


class HomeKitPairingSensor(MysaEntity, SensorEntity):
    """Whether the thermostat is paired locally, or reached only via the cloud."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_options = ["paired", "not_paired"]
    _attr_translation_key = "homekit_pairing"

    def __init__(self, thermostat: Thermostat) -> None:
        """Create the entity."""
        super().__init__(thermostat, "homekit_pairing")

    @property
    def available(self) -> bool:
        """Always known."""
        return True

    @property
    def native_value(self) -> str:
        """Paired or not_paired."""
        return "paired" if self.thermostat.has_local else "not_paired"


class MysaAccountSensor(MysaEntity, SensorEntity):
    """Whether the thermostat is on the signed-in Mysa account."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_options = [ACCOUNT_ON, ACCOUNT_OFF]
    _attr_translation_key = "mysa_account"

    def __init__(self, thermostat: Thermostat) -> None:
        """Create the entity."""
        super().__init__(thermostat, "mysa_account")

    @property
    def available(self) -> bool:
        """Available while the cloud's thermostat list is known."""
        return self.thermostat.account_status is not None

    @property
    def native_value(self) -> str | None:
        """on_account or not_on_account."""
        return self.thermostat.account_status


class WifiSignalSensor(MysaCloudEntity, SensorEntity):
    """Wi-Fi signal as the thermostat reports it to the cloud."""

    _attr_device_class = SensorDeviceClass.SIGNAL_STRENGTH
    _attr_native_unit_of_measurement = SIGNAL_STRENGTH_DECIBELS_MILLIWATT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False
    _attr_translation_key = "wifi_signal"

    def __init__(self, thermostat: Thermostat) -> None:
        """Create the entity."""
        super().__init__(thermostat, "wifi_signal")

    @property
    def available(self) -> bool:
        """Only expose RSSI while its telemetry reading is fresh."""
        return self.thermostat.cloud_ok

    @property
    def native_value(self) -> int | None:
        """RSSI in dBm."""
        state = self.cloud_state
        return state.rssi if state else None


class RuntimeExtraData(ExtraStoredData):
    """Local runtime count saved across restarts."""

    def __init__(self, day: date, seconds: float) -> None:
        """Store the day and the locally counted seconds."""
        self.day = day
        self.seconds = seconds

    def as_dict(self) -> dict[str, Any]:
        """Serialize."""
        return {"day": self.day.isoformat(), "seconds": self.seconds}


class _EveryMinute(MysaEntity):
    """Writes its state once a minute, not on every thermostat update.

    Runtime and energy move with every update while heating (several a
    minute), and statistics only need them once a minute.
    """

    async def async_added_to_hass(self) -> None:
        """Start the minute timer."""
        await super().async_added_to_hass()
        self.async_on_remove(
            async_track_time_interval(self.hass, self._refresh, RUNTIME_REFRESH)
        )

    @callback
    def _handle_update(self) -> None:
        """Nothing to do: the minute timer writes the state."""

    @callback
    def _refresh(self, _now: datetime) -> None:
        self.async_write_ha_state()


class HeatingRuntimeSensor(_EveryMinute, RestoreSensor):
    """Hours of heating since local midnight; resets daily."""

    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.HOURS
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_suggested_display_precision = 2
    _attr_translation_key = "heating_runtime_today"

    def __init__(self, thermostat: Thermostat) -> None:
        """Create the entity."""
        super().__init__(thermostat, "heating_runtime_today")

    @property
    def available(self) -> bool:
        """The local count is always meaningful, even while offline."""
        return True

    async def async_added_to_hass(self) -> None:
        """Restore today's local count."""
        await super().async_added_to_hass()
        if (stored := await self.async_get_last_extra_data()) is not None:
            data = stored.as_dict()
            try:
                day = date.fromisoformat(str(data.get("day")))
                seconds = float(data.get("seconds", 0))
            except TypeError, ValueError:
                pass
            else:
                self.thermostat.runtime.restore(day, seconds)

    @property
    def extra_restore_state_data(self) -> SensorExtraStoredData | RuntimeExtraData:  # type: ignore[override]
        """Save the local count, not just the displayed value."""
        tracker = self.thermostat.runtime
        return RuntimeExtraData(tracker.day, tracker.total())

    @property
    def native_value(self) -> float:
        """Hours today."""
        return round(self.thermostat.runtime_today / 3600, 4)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Where the number comes from."""
        return {"source": self.thermostat.runtime_source}


class HeaterPowerSensor(MysaEntity, SensorEntity):
    """Heater power now: the wattage set for this thermostat while it heats."""

    _attr_device_class = SensorDeviceClass.POWER
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 0
    _attr_translation_key = "heater_power"

    def __init__(self, thermostat: Thermostat) -> None:
        """Create the entity."""
        super().__init__(thermostat, "heater_power")

    @property
    def native_value(self) -> float | None:
        """W: relay load locally, or average load over the cloud reading interval."""
        fraction = self.thermostat.heat_fraction
        return self.thermostat.heater_watts * fraction if fraction is not None else None


class EnergySensor(_EveryMinute, SensorEntity):
    """Heater energy today: runtime x the wattage set for this thermostat."""

    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_suggested_display_precision = 3
    _attr_translation_key = "energy_today"

    def __init__(self, thermostat: Thermostat) -> None:
        """Create the entity."""
        super().__init__(thermostat, "energy_today")

    @property
    def available(self) -> bool:
        """Derived from the runtime, so always available."""
        return True

    @property
    def native_value(self) -> float:
        """KWh today."""
        hours = self.thermostat.runtime_today / 3600
        return round(hours * self.thermostat.heater_watts / 1000, 4)
