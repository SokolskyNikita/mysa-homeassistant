"""Display units (local) and heater type (cloud)."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from mysa_client.cloud import HeaterType

from .data import MysaConfigEntry
from .entity import MysaCloudEntity, MysaEntity, async_add_when_ready
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
        if thermostat.supports_display_unit:
            entities.append(DisplayUnitSelect(thermostat))
        caps = thermostat.capabilities
        if thermostat.hvac_fan_speeds:
            entities.append(HvacFanSpeedSelect(thermostat))
        if (
            not thermostat.basic_cloud_only
            and caps is not None
            and set(HEATER_OPTIONS) <= set(caps.control_types)
        ):
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
    def available(self) -> bool:
        """Available through HomeKit or a connected cloud path."""
        return self.thermostat.local_ok or self.thermostat.cloud_connected

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
        await self.thermostat.async_set_heater_type(HEATER_BY_OPTION[option])


HVAC_FAN_SPEEDS = {
    0: "off",
    **{n: f"speed_{n}" for n in range(1, 8)},
    8: "sleep",
    9: "auto",
}


class HvacFanSpeedSelect(MysaCloudEntity, SelectEntity):
    """Native Central HVAC speeds, separate from fan on/auto/circulation."""

    _attr_translation_key = "hvac_fan_speed"

    def __init__(self, thermostat: Thermostat) -> None:
        """Create the optional speed control."""
        super().__init__(thermostat, "hvac_fan_speed")

    @property
    def available(self) -> bool:
        """Unavailable in modes without supported fan speeds."""
        return super().available and bool(self.thermostat.hvac_fan_speeds)

    @property
    def options(self) -> list[str]:
        """Only the speeds the capability document allows in this mode."""
        return [HVAC_FAN_SPEEDS[n] for n in self.thermostat.hvac_fan_speeds]

    @property
    def current_option(self) -> str | None:
        """Reported native fan speed."""
        state = self.cloud_state
        return (
            HVAC_FAN_SPEEDS.get(state.fan_mode)
            if state and state.fan_mode is not None
            else None
        )

    async def async_select_option(self, option: str) -> None:
        """The command boundary rechecks against the latest mode intent."""
        speed = next(n for n, label in HVAC_FAN_SPEEDS.items() if label == option)
        await self.thermostat.async_set_hvac_fan_speed(speed)
