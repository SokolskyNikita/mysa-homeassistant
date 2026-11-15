"""Display brightness, hardware setpoint limits and temperature offset."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from mysa_client.cloud import (
    Capabilities,
    DeviceState,
    SetpointRange,
    fahrenheit_bounds,
)
from mysa_client.cloud.device import BRIGHTNESS_STEP, MIN_WAKE_BRIGHTNESS

from .cloud_coordinator import translated_error
from .data import MysaConfigEntry
from .entity import MysaCloudEntity, MysaEntity, async_add_when_ready
from .temperature import (
    Unit,
    display_setpoint,
    ha_unit,
)
from .thermostat import Thermostat

PARALLEL_UPDATES = 1


@dataclass(frozen=True, kw_only=True)
class MysaNumberDescription(NumberEntityDescription):
    """How to read, range and write one number."""

    value: Callable[[DeviceState], float | None]
    supported: Callable[[Capabilities], bool]
    value_range: Callable[[Capabilities], tuple[float, float, float] | None]
    set_value: Callable[[Thermostat, float, Unit], Awaitable[object]]
    # Temperatures follow the owner's unit: whole °F for Fahrenheit owners.
    temperature: Literal["setpoint", "offset"] | None = None
    rules_range: Callable[[Thermostat], SetpointRange] | None = None


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
        set_value=lambda thermostat, value, _unit: thermostat.async_set_brightness(
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
        set_value=lambda thermostat, value, _unit: thermostat.async_set_brightness(
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
        set_value=lambda thermostat, value, unit: thermostat.async_set_setpoint_limit(
            value, minimum=True, unit=unit
        ),
        temperature="setpoint",
        rules_range=lambda thermostat: thermostat.rules.hardware_heat_range,
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
        set_value=lambda thermostat, value, unit: thermostat.async_set_setpoint_limit(
            value, minimum=False, unit=unit
        ),
        temperature="setpoint",
        rules_range=lambda thermostat: thermostat.rules.hardware_heat_range,
    ),
    MysaNumberDescription(
        key="temperature_offset",
        translation_key="temperature_offset",
        entity_category=EntityCategory.CONFIG,
        # Always °C, as before 0.9.0, so automations keep their meaning. The
        # difference class lets an owner choose °F for the entity, and HA then
        # converts it as a difference (±5 °C shows as ±9 °F).
        device_class=NumberDeviceClass.TEMPERATURE_DELTA,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        mode=NumberMode.BOX,
        value=lambda s: s.temperature_offset,
        supported=lambda c: c.offset_range is not None,
        value_range=lambda c: c.offset_range,
        set_value=lambda thermostat, value, unit: (
            thermostat.async_set_temperature_offset(value, unit=unit)
        ),
        temperature="offset",
        rules_range=lambda thermostat: thermostat.rules.offset_range,
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
        description = self.entity_description
        caps = self.thermostat.capabilities
        rng = description.value_range(caps) if caps else None
        low, high, step = rng or (0.0, 100.0, 1.0)
        if description.temperature is not None and description.rules_range:
            limits = description.rules_range(self.thermostat)
            r_low: float | None
            r_high: float | None
            if self._unit == "F":
                r_low, r_high = fahrenheit_bounds(limits)
                step = 1.0
            else:
                r_low, r_high, step = limits.minimum, limits.maximum, limits.step
            low = low if r_low is None else r_low
            high = high if r_high is None else r_high
        return low, high, step

    @property
    def _unit(self) -> Unit:
        """The owner's unit for the setpoint limits; °C for everything else."""
        if self.entity_description.temperature == "setpoint":
            return self.owner_unit
        return "C"

    @property
    def native_unit_of_measurement(self) -> str | None:
        """The owner's unit for the limits; a plain property, never cached."""
        if self.entity_description.temperature == "setpoint":
            return ha_unit(self.owner_unit)
        return self.entity_description.native_unit_of_measurement

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
        """Current value; the setpoint limits are in the owner's unit."""
        state = self.cloud_state
        if state is None:
            return None
        key = self.entity_description.key
        if key == "setpoint_min":
            return display_setpoint(self.thermostat.setpoint_min, self.owner_unit)
        if key == "setpoint_max":
            return display_setpoint(self.thermostat.setpoint_max, self.owner_unit)
        return self.entity_description.value(state)

    async def async_set_native_value(self, value: float) -> None:
        """Write the new value (the setpoint limits in the owner's unit)."""
        state = self.cloud_state
        if state is None:
            raise translated_error("cloud_required", name=self.thermostat.name)
        # Limits are strict: one outside the hardware range, or not strictly
        # inside the other limit, is refused by the rules in the owner's unit.
        async with self._service_call():
            await self.entity_description.set_value(self.thermostat, value, self._unit)
