"""Identify button (local)."""

from __future__ import annotations

from homeassistant.components.button import ButtonDeviceClass, ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .data import MysaConfigEntry
from .entity import MysaEntity, async_add_when_ready
from .thermostat import Thermostat

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MysaConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add Identify for thermostats paired over HomeKit."""
    async_add_when_ready(
        entry,
        async_add_entities,
        lambda t: [IdentifyButton(t)] if t.has_local else [],
    )


class IdentifyButton(MysaEntity, ButtonEntity):
    """Makes the thermostat signal itself."""

    _attr_device_class = ButtonDeviceClass.IDENTIFY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, thermostat: Thermostat) -> None:
        """Create the entity."""
        super().__init__(thermostat, "identify")

    @property
    def available(self) -> bool:
        """Needs the local connection."""
        return self.thermostat.local_ok

    async def async_press(self) -> None:
        """Identify."""
        await self.thermostat.async_identify()
