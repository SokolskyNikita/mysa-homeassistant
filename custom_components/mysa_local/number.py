"""Display brightness, hardware setpoint limits and temperature offset."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import Capabilities, DeviceState
from .cloud import translated_error
from .data import MysaConfigEntry
from .entity import MysaCloudEntity, MysaEntity, async_add_when_ready
from .thermostat import Thermostat

PARALLEL_UPDATES = 1


@dataclass(frozen=True, kw_only=True)
class MysaNumberDescription(NumberEntityDescription):
    """How to read, range and write one number."""

    value: Callable[[DeviceState], float | None]
    supported: Callable[[Capabilities], bool]
    value_range: Callable[[Capabilities], tuple[float, float, float] | None]
    changes: Callable[[DeviceState, float], dict[str, dict[str, Any]]]


# Mysa's app (4.20.1): brightness in steps of 5, wake brightness never below 10,
# and sleep never brighter than wake. It never sends anything else.
BRIGHTNESS_STEP = 5
MIN_WAKE_BRIGHTNESS = 10


def _brightness_changes(
    key: str,
) -> Callable[[DeviceState, float], dict[str, dict[str, Any]]]:
    def _changes(state: DeviceState, value: float) -> dict[str, dict[str, Any]]:
        # Mysa's app always sends both brightness values together.
        active = state.active_brightness if state.active_brightness is not None else 100
        idle = state.idle_brightness if state.idle_brightness is not None else 50
        value = round(value / BRIGHTNESS_STEP) * BRIGHTNESS_STEP
        # Keep the value just set and move the other one if they'd cross (the
        # app swaps them instead, which would undo what was just set here).
        if key == "activeIntensity":
            active = max(MIN_WAKE_BRIGHTNESS, value)
            idle = min(idle, active)
        else:
            idle = value
            active = max(active, idle)
        return {"physicalInterface": {"activeIntensity": active, "idleIntensity": idle}}

    return _changes


NUMBERS: tuple[MysaNumberDescription, ...] = (
    MysaNumberDescription(
        key="brightness_active",
        translation_key="brightness_active",
        entity_category=EntityCategory.CONFIG,
        native_unit_of_measurement=PERCENTAGE,
        mode=NumberMode.SLIDER,
        value=lambda s: s.active_brightness,
        supported=lambda c: c.brightness,
        value_range=lambda c: (MIN_WAKE_BRIGHTNESS, 100, BRIGHTNESS_STEP),
        changes=_brightness_changes("activeIntensity"),
    ),
    MysaNumberDescription(
        key="brightness_idle",
        translation_key="brightness_idle",
        entity_category=EntityCategory.CONFIG,
        native_unit_of_measurement=PERCENTAGE,
        mode=NumberMode.SLIDER,
        value=lambda s: s.idle_brightness,
        supported=lambda c: c.brightness,
        value_range=lambda c: (0, 100, BRIGHTNESS_STEP),
        changes=_brightness_changes("idleIntensity"),
    ),
    MysaNumberDescription(
        key="setpoint_min",
        translation_key="setpoint_min",
        entity_category=EntityCategory.CONFIG,
        device_class=NumberDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        mode=NumberMode.BOX,
        value=lambda s: s.setpoint_min,
        supported=lambda c: c.heat_setpoint_range is not None,
        value_range=lambda c: c.heat_setpoint_range,
        changes=lambda s, v: {"targetHeat": {"lockoutMin": v}},
    ),
    MysaNumberDescription(
        key="setpoint_max",
        translation_key="setpoint_max",
        entity_category=EntityCategory.CONFIG,
        device_class=NumberDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        mode=NumberMode.BOX,
        value=lambda s: s.setpoint_max,
        supported=lambda c: c.heat_setpoint_range is not None,
        value_range=lambda c: c.heat_setpoint_range,
        changes=lambda s, v: {"targetHeat": {"lockoutMax": v}},
    ),
    MysaNumberDescription(
        key="temperature_offset",
        translation_key="temperature_offset",
        entity_category=EntityCategory.CONFIG,
        # A difference, not a temperature: no device class, so °F users aren't
        # shown a converted absolute value.
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        mode=NumberMode.BOX,
        value=lambda s: s.temperature_offset,
        supported=lambda c: c.offset_range is not None,
        value_range=lambda c: c.offset_range,
        changes=lambda s, v: {"tracking": {"ambientOffset": v}},
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MysaConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the numbers each model supports."""

    def _entities(thermostat: Thermostat) -> list[MysaEntity]:
        caps = thermostat.capabilities
        if caps is None or thermostat.cloud_state is None:
            return []
        return [
            MysaNumber(thermostat, description)
            for description in NUMBERS
            if description.supported(caps)
        ]

    async_add_when_ready(entry, async_add_entities, _entities)


class MysaNumber(MysaCloudEntity, NumberEntity):
    """A numeric thermostat setting (cloud)."""

    entity_description: MysaNumberDescription

    def __init__(
        self, thermostat: Thermostat, description: MysaNumberDescription
    ) -> None:
        """Create the entity."""
        super().__init__(thermostat, description.key, description)

    def _range(self) -> tuple[float, float, float]:
        caps = self.thermostat.capabilities
        rng = self.entity_description.value_range(caps) if caps else None
        return rng or (0.0, 100.0, 1.0)

    @property
    def native_min_value(self) -> float:
        """Lowest allowed value."""
        return self._range()[0]

    @property
    def native_max_value(self) -> float:
        """Highest allowed value."""
        return self._range()[1]

    @property
    def native_step(self) -> float:
        """Step."""
        return self._range()[2]

    @property
    def native_value(self) -> float | None:
        """Current value."""
        state = self.cloud_state
        if state is None:
            return None
        if self.entity_description.key == "setpoint_min":
            return self.thermostat.setpoint_min
        if self.entity_description.key == "setpoint_max":
            return self.thermostat.setpoint_max
        return self.entity_description.value(state)

    async def async_set_native_value(self, value: float) -> None:
        """Write the new value."""
        state = self.cloud_state
        if state is None:
            raise translated_error("cloud_required", name=self.thermostat.name)
        key = self.entity_description.key
        if key == "setpoint_min" and (
            (setpoint_max := self.thermostat.setpoint_max) is not None
            and value >= setpoint_max
        ):
            raise translated_error("limits_invalid")
        if key == "setpoint_max" and (
            (setpoint_min := self.thermostat.setpoint_min) is not None
            and value <= setpoint_min
        ):
            raise translated_error("limits_invalid")
        await self.thermostat.async_cloud_update(
            self.entity_description.changes(state, value)
        )
