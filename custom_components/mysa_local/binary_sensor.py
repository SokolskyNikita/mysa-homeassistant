"""Binary sensors: cloud connectivity and schedule hold."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .data import MysaConfigEntry
from .entity import MysaCloudEntity, MysaEntity, async_add_when_ready
from .thermostat import Thermostat

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MysaConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add cloud binary sensors once the cloud knows the thermostat."""

    def _entities(thermostat: Thermostat) -> list[MysaEntity]:
        if thermostat.cloud_state is None:
            return []
        entities: list[MysaEntity] = [CloudOnlineSensor(thermostat)]
        caps = thermostat.capabilities
        if not thermostat.basic_cloud_only and caps is not None and caps.scheduling:
            entities.append(ScheduleHoldSensor(thermostat))
        return entities

    async_add_when_ready(entry, async_add_entities, _entities)


class CloudOnlineSensor(MysaCloudEntity, BinarySensorEntity):
    """Whether Mysa's cloud currently hears from the thermostat."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "cloud_online"

    def __init__(self, thermostat: Thermostat) -> None:
        """Create the entity."""
        super().__init__(thermostat, "cloud_online")

    @property
    def available(self) -> bool:
        """Available while the cloud answers (the thermostat may be offline)."""
        cloud = self.thermostat.cloud
        return (
            cloud is not None
            and cloud.last_update_success
            and self.cloud_state is not None
        )

    @property
    def is_on(self) -> bool | None:
        """True if the thermostat is connected to Mysa's cloud."""
        state = self.cloud_state
        return state.connected if state else None


class ScheduleHoldSensor(MysaCloudEntity, BinarySensorEntity):
    """On while a hold overrides the thermostat's Mysa schedule."""

    _attr_translation_key = "schedule_hold"

    def __init__(self, thermostat: Thermostat) -> None:
        """Create the entity."""
        super().__init__(thermostat, "schedule_hold")

    @property
    def available(self) -> bool:
        """Available while the cloud answers."""
        cloud = self.thermostat.cloud
        return cloud is not None and cloud.last_update_success

    @property
    def is_on(self) -> bool:
        """True while a hold is active."""
        return self.thermostat.hold is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Hold type and end time, as Mysa reports them."""
        hold = self.thermostat.hold
        if not hold:
            return None
        keys = ("holdType", "startTime", "endTime")
        return {k: hold[k] for k in keys if k in hold}
