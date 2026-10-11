"""The thermostat itself."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.climate import ClimateEntity
from homeassistant.components.climate.const import (
    ATTR_HVAC_MODE,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.const import ATTR_TEMPERATURE, PRECISION_TENTHS, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import Mode
from .data import MysaConfigEntry
from .entity import MysaEntity, async_add_when_ready
from .schedule import (
    async_clear_schedule,
    async_hold,
    async_resume_schedule,
    async_set_schedule,
)
from .thermostat import (
    ACTION_COOLING,
    ACTION_HEATING,
    ACTION_IDLE,
    ACTION_OFF,
    Thermostat,
)

PARALLEL_UPDATES = 0

MODE_TO_HVAC: dict[Mode, HVACMode] = {
    Mode.OFF: HVACMode.OFF,
    Mode.HEAT: HVACMode.HEAT,
    Mode.COOL: HVACMode.COOL,
    Mode.FAN_ONLY: HVACMode.FAN_ONLY,
    Mode.DRY: HVACMode.DRY,
}
HVAC_TO_MODE = {v: k for k, v in MODE_TO_HVAC.items()}
ACTION_TO_HVAC = {
    ACTION_OFF: HVACAction.OFF,
    ACTION_IDLE: HVACAction.IDLE,
    ACTION_HEATING: HVACAction.HEATING,
    ACTION_COOLING: HVACAction.COOLING,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MysaConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """One climate entity per thermostat."""
    async_add_when_ready(entry, async_add_entities, lambda t: [MysaClimate(t)])


class MysaClimate(MysaEntity, ClimateEntity):
    """A Mysa thermostat."""

    _attr_name = None
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_precision = PRECISION_TENTHS

    def __init__(self, thermostat: Thermostat) -> None:
        """Create the entity."""
        super().__init__(thermostat, "climate")

    @property
    def supported_features(self) -> ClimateEntityFeature:
        """Setpoint plus on/off."""
        features = ClimateEntityFeature.TURN_ON | ClimateEntityFeature.TURN_OFF
        if self.thermostat.mode not in (Mode.FAN_ONLY, Mode.DRY):
            features |= ClimateEntityFeature.TARGET_TEMPERATURE
        return features

    @property
    def hvac_modes(self) -> list[HVACMode]:
        """Modes from the model's capability data."""
        modes = [
            MODE_TO_HVAC[m]
            for m in self.thermostat.supported_modes
            if m in MODE_TO_HVAC
        ]
        return modes or [HVACMode.OFF, HVACMode.HEAT]

    @property
    def hvac_mode(self) -> HVACMode | None:
        """Current mode."""
        mode = self.thermostat.mode
        return MODE_TO_HVAC.get(mode) if mode is not None else None

    @property
    def hvac_action(self) -> HVACAction | None:
        """What the relay is doing right now."""
        action = self.thermostat.action
        return ACTION_TO_HVAC.get(action) if action else None

    @property
    def current_temperature(self) -> float | None:
        """Room temperature."""
        return self.thermostat.current_temperature

    @property
    def current_humidity(self) -> float | None:
        """Room humidity, while the cloud reports it."""
        return self.thermostat.humidity if self.thermostat.cloud_ok else None

    @property
    def target_temperature(self) -> float | None:
        """Setpoint."""
        return self.thermostat.target_temperature

    @property
    def min_temp(self) -> float:
        """Lowest setpoint (the hardware limit when the cloud knows it)."""
        return self.thermostat.setpoint_range[0]

    @property
    def max_temp(self) -> float:
        """Highest setpoint."""
        return self.thermostat.setpoint_range[1]

    @property
    def target_temperature_step(self) -> float:
        """Setpoint step (0.5 °C on Mysa)."""
        return self.thermostat.setpoint_range[2]

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Change the setpoint (and the mode, if given)."""
        if (hvac_mode := kwargs.get(ATTR_HVAC_MODE)) is not None:
            await self.async_set_hvac_mode(hvac_mode)
        if (temperature := kwargs.get(ATTR_TEMPERATURE)) is not None:
            await self.thermostat.async_set_temperature(float(temperature))

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Change the mode."""
        await self.thermostat.async_set_mode(HVAC_TO_MODE[hvac_mode])

    async def async_turn_on(self) -> None:
        """Turn on in the first non-off mode (heat on heaters)."""
        for mode in self.thermostat.supported_modes:
            if mode != Mode.OFF:
                await self.thermostat.async_set_mode(mode)
                return
        await self.thermostat.async_set_mode(Mode.HEAT)

    async def async_turn_off(self) -> None:
        """Turn off."""
        await self.thermostat.async_set_mode(Mode.OFF)

    # ---- entity actions (registered in services.py) --------------------

    async def async_hold(
        self,
        temperature: float | None = None,
        hvac_mode: HVACMode | None = None,
        until: datetime | None = None,
    ) -> None:
        """Hold a setpoint over the Mysa schedule."""
        mode = HVAC_TO_MODE[hvac_mode] if hvac_mode else None
        await async_hold(self.hass, self.thermostat, temperature, mode, until)

    async def async_resume_schedule(self) -> None:
        """End a hold."""
        await async_resume_schedule(self.thermostat)

    async def async_set_schedule(self, events: list[dict[str, Any]]) -> None:
        """Replace the thermostat's weekly schedule."""
        await async_set_schedule(self.thermostat, events)

    async def async_clear_schedule(self) -> None:
        """Delete the thermostat's schedule."""
        await async_clear_schedule(self.thermostat)
