"""Button lock."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import ButtonLock
from .data import MysaConfigEntry
from .entity import MysaCloudEntity, MysaEntity, async_add_when_ready
from .thermostat import Thermostat

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MysaConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the button lock where the model supports it."""

    def _entities(thermostat: Thermostat) -> list[MysaEntity]:
        caps = thermostat.capabilities
        if caps is None or ButtonLock.LOCKED not in caps.lockout_values:
            return []
        return [ButtonLockSwitch(thermostat)]

    async_add_when_ready(entry, async_add_entities, _entities)


class ButtonLockSwitch(MysaCloudEntity, SwitchEntity):
    """Locks the thermostat's own buttons."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_translation_key = "button_lock"

    def __init__(self, thermostat: Thermostat) -> None:
        """Create the entity."""
        super().__init__(thermostat, "button_lock")

    @property
    def is_on(self) -> bool | None:
        """True while the buttons are locked."""
        state = self.cloud_state
        if state is None or state.button_lock is None:
            return None
        return state.button_lock != ButtonLock.UNLOCKED

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Lock the buttons."""
        await self.thermostat.async_cloud_update(
            {"physicalInterface": {"lockout": int(ButtonLock.LOCKED)}}
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Unlock the buttons."""
        await self.thermostat.async_cloud_update(
            {"physicalInterface": {"lockout": int(ButtonLock.UNLOCKED)}}
        )
