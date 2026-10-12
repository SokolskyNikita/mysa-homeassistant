"""Display brightness, hardware setpoint limits and temperature offset."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from mysa_client.cloud import Capabilities, DeviceState
from mysa_client.cloud.device import BRIGHTNESS_STEP, MIN_WAKE_BRIGHTNESS

from .cloud_coordinator import translated_error
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
    set_value: Callable[[Thermostat, float], Awaitable[None]]


# Mysa's app (4.20.1): brightness in steps of 5 and wake brightness never below
# 10. These values describe the controls; the thermostat owns paired-value
# normalization and the cloud protocol owns the request format.


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
        set_value=lambda thermostat, value: thermostat.async_set_brightness(
            value, active=True
        ),
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
        set_value=lambda thermostat, value: thermostat.async_set_brightness(
            value, active=False
        ),
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
        set_value=lambda thermostat, value: thermostat.async_set_setpoint_limit(
            value, minimum=True
        ),
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
        set_value=lambda thermostat, value: thermostat.async_set_setpoint_limit(
            value, minimum=False
        ),
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
        set_value=lambda thermostat, value: thermostat.async_set_temperature_offset(
            value
        ),
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
        if (
            caps is None
            or thermostat.cloud_state is None
            or thermostat.is_climate_sensor
        ):
            return []
        return [
            MysaNumber(thermostat, description)
            for description in NUMBERS
            if description.supported(caps)
            and (
                not thermostat.basic_cloud_only
                or description.key in ("brightness_active", "brightness_idle")
            )
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
        await self.entity_description.set_value(self.thermostat, value)
