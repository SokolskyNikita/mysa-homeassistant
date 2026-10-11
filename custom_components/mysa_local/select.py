"""Display units (local) and heater type (cloud)."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import HeaterType
from .data import MysaConfigEntry
from .entity import MysaCloudEntity, MysaEntity, async_add_when_ready
from .local import DISPLAY_UNITS
from .thermostat import Thermostat

PARALLEL_UPDATES = 1

UNIT_OPTIONS = {"C": "celsius", "F": "fahrenheit"}
UNIT_BY_OPTION = {v: k for k, v in UNIT_OPTIONS.items()}
HEATER_OPTIONS = {
    HeaterType.BASEBOARD: "baseboard",
    HeaterType.FAN_FORCED: "fan_forced",
}
HEATER_BY_OPTION = {v: k for k, v in HEATER_OPTIONS.items()}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MysaConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the selects."""

    def _entities(thermostat: Thermostat) -> list[MysaEntity]:
        entities: list[MysaEntity] = []
        local_units = (
            thermostat.local is not None and DISPLAY_UNITS in thermostat.local.chars
        )
        caps = thermostat.capabilities
        if local_units or (caps is not None and {"C", "F"} <= set(caps.units)):
            entities.append(DisplayUnitSelect(thermostat))
        if caps is not None and set(HEATER_OPTIONS) <= set(caps.control_types):
            entities.append(HeaterTypeSelect(thermostat))
        return entities

    async_add_when_ready(entry, async_add_entities, _entities)


class DisplayUnitSelect(MysaEntity, SelectEntity):
    """°C or °F on the thermostat's own screen."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_translation_key = "display_unit"
    _attr_options = list(UNIT_OPTIONS.values())

    def __init__(self, thermostat: Thermostat) -> None:
        """Create the entity."""
        super().__init__(thermostat, "display_unit")

    @property
    def current_option(self) -> str | None:
        """Celsius or fahrenheit."""
        unit = self.thermostat.display_unit
        return UNIT_OPTIONS.get(unit) if unit else None

    async def async_select_option(self, option: str) -> None:
        """Switch units."""
        await self.thermostat.async_set_display_unit(UNIT_BY_OPTION[option])


class HeaterTypeSelect(MysaCloudEntity, SelectEntity):
    """Baseboard or fan-forced heater (sets how often the relay switches)."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_translation_key = "heater_type"
    _attr_options = list(HEATER_OPTIONS.values())

    def __init__(self, thermostat: Thermostat) -> None:
        """Create the entity."""
        super().__init__(thermostat, "heater_type")

    @property
    def current_option(self) -> str | None:
        """Baseboard or fan_forced."""
        record = self.thermostat.cloud_record
        value = record.duty_cycle_opt if record else None
        if value is None and (state := self.cloud_state) is not None:
            value = state.control_type
        try:
            return HEATER_OPTIONS.get(HeaterType(value)) if value is not None else None
        except ValueError:
            return None

    async def async_select_option(self, option: str) -> None:
        """Change the heater type."""
        await self.thermostat.async_cloud_settings(
            {"DutyCycleOpt": int(HEATER_BY_OPTION[option])}
        )
