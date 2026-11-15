"""The thermostat itself."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.climate import ClimateEntity
from homeassistant.components.climate.const import (
    ATTR_HVAC_MODE,
    ATTR_TARGET_TEMP_HIGH,
    ATTR_TARGET_TEMP_LOW,
    FAN_AUTO,
    FAN_HIGH,
    FAN_LOW,
    FAN_MEDIUM,
    SWING_OFF,
    SWING_ON,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.const import (
    ATTR_TEMPERATURE,
    EVENT_LOGBOOK_ENTRY,
    PRECISION_TENTHS,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from mysa_client.cloud import (
    SWING_HORIZONTAL,
    SWING_VERTICAL,
    AcFan,
    Mode,
    Setpoint,
    Swing,
)
from mysa_client.cloud.setpoints import to_celsius

from .const import DOMAIN, LOGGER
from .data import MysaConfigEntry
from .entity import MysaEntity, async_add_when_ready
from .schedule import (
    async_clear_schedule,
    async_hold,
    async_resume_schedule,
    async_set_schedule,
)
from .temperature import (
    Unit,
    display_reading,
    display_setpoint,
    format_requested,
    format_setpoint,
    ha_unit,
)
from .thermostat import (
    ACTION_COOLING,
    ACTION_HEATING,
    ACTION_IDLE,
    ACTION_OFF,
    HVAC_TO_MODE,
    MODE_TO_HVAC,
    Thermostat,
)

PARALLEL_UPDATES = 0

# Fan and swing names; the integration's translations label the custom ones.
FAN_NAMES: dict[AcFan, str] = {
    AcFan.AUTO: FAN_AUTO,
    AcFan.LOW: FAN_LOW,
    AcFan.MEDIUM: FAN_MEDIUM,
    AcFan.HIGH: FAN_HIGH,
    **{AcFan[f"SPEED_{n}"]: f"speed_{n}" for n in range(1, 8)},
    AcFan.SLEEP: "sleep",
}
FAN_BY_NAME = {v: k for k, v in FAN_NAMES.items()}
SWING_NAMES: dict[Swing, str] = {
    Swing.OFF: SWING_OFF,
    Swing.ON: SWING_ON,
    Swing.AUTO: "auto",
    **{Swing[f"POSITION_{n}"]: f"position_{n}" for n in range(1, 8)},
}
SWING_BY_NAME = {v: k for k, v in SWING_NAMES.items()}
ACTION_TO_HVAC = {
    ACTION_OFF: HVACAction.OFF,
    "fan": HVACAction.FAN,
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
    async_add_when_ready(
        entry,
        async_add_entities,
        lambda t: (
            [MysaClimate(t)]
            if not t.is_climate_sensor and (not t.basic_cloud_only or t.supported_modes)
            else []
        ),
    )


class MysaClimate(MysaEntity, ClimateEntity):
    """A Mysa thermostat."""

    _attr_name = None
    _attr_translation_key = "thermostat"
    _attr_precision = PRECISION_TENTHS

    def __init__(self, thermostat: Thermostat) -> None:
        """Create the entity."""
        super().__init__(thermostat, "climate")

    @property
    def supported_features(self) -> ClimateEntityFeature:
        """Setpoint and on/off, plus fan and swing on a Mini-Split AC."""
        modes = self.thermostat.supported_modes
        if self.thermostat.basic_cloud_only:
            features = ClimateEntityFeature(0)
            if Mode.OFF in modes:
                features |= ClimateEntityFeature.TURN_OFF
            if any(mode != Mode.OFF for mode in modes):
                features |= ClimateEntityFeature.TURN_ON
            if Mode.HEAT in modes or Mode.COOL in modes:
                features |= ClimateEntityFeature.TARGET_TEMPERATURE
            if self.thermostat.is_hvac and Mode.AUTO in modes:
                features |= ClimateEntityFeature.TARGET_TEMPERATURE_RANGE
        else:
            features = ClimateEntityFeature.TURN_ON | ClimateEntityFeature.TURN_OFF
            if self.thermostat.mode not in (Mode.FAN_ONLY, Mode.DRY, Mode.AUTO):
                features |= ClimateEntityFeature.TARGET_TEMPERATURE
        # Mini-Split AC only, in modes where its remote has them.
        if self.thermostat.fan_speeds or self.thermostat.hvac_fan_modes:
            features |= ClimateEntityFeature.FAN_MODE
        if self.thermostat.swing_options(SWING_VERTICAL):
            features |= ClimateEntityFeature.SWING_MODE
        if self.thermostat.swing_options(SWING_HORIZONTAL):
            features |= ClimateEntityFeature.SWING_HORIZONTAL_MODE
        return features

    @property
    def hvac_modes(self) -> list[HVACMode]:
        """Modes from the model's capability data."""
        return self.thermostat.hvac_modes

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

    # Temperatures are reported natively in the owner's unit, so a Fahrenheit
    # owner sees whole °F as the Mysa app rounds them (22.5 °C is 73 °F, where
    # HA's own conversion would show 72.5) and HA hands the picked whole °F
    # straight to the setpoint rules. HA 2026.11 reads the native_ names; the
    # old names keep 2026.10 working and lose to the native ones on 2026.11.

    @property
    def native_temperature_unit(self) -> str:
        """The owner's unit; a plain property, since the base class caches it."""
        return ha_unit(self.owner_unit)

    @property
    def temperature_unit(self) -> str:
        """Pre-2026.11 name for native_temperature_unit."""
        return self.native_temperature_unit

    @property
    def native_current_temperature(self) -> float | None:
        """Room temperature, to one decimal in °F."""
        return display_reading(self.thermostat.current_temperature, self.owner_unit)

    @property
    def current_temperature(self) -> float | None:
        """Pre-2026.11 name for native_current_temperature."""
        return self.native_current_temperature

    @property
    def current_humidity(self) -> float | None:
        """Room humidity, preferring fresh MQTT independently of REST health."""
        if self.thermostat.humidity_source == "mqtt":
            return self.thermostat.humidity
        return self.thermostat.humidity if self.thermostat.cloud_ok else None

    @property
    def native_target_temperature(self) -> float | None:
        """Setpoint: whole °F (halfway up, like the Mysa app) or °C."""
        return display_setpoint(self.thermostat.target_temperature, self.owner_unit)

    @property
    def target_temperature(self) -> float | None:
        """Pre-2026.11 name for native_target_temperature."""
        return self.native_target_temperature

    def _auto_target(self, *, heat: bool) -> float | None:
        state = self.thermostat.cloud_state
        if not (self.thermostat.is_hvac and state and state.mode == Mode.AUTO):
            return None
        value = state.auto_heat_setpoint if heat else state.auto_cool_setpoint
        return display_setpoint(value, self.owner_unit)

    @property
    def native_target_temperature_low(self) -> float | None:
        """Confirmed heating target in Central HVAC auto mode."""
        return self._auto_target(heat=True)

    @property
    def target_temperature_low(self) -> float | None:
        """Pre-2026.11 name for native_target_temperature_low."""
        return self.native_target_temperature_low

    @property
    def native_target_temperature_high(self) -> float | None:
        """Confirmed cooling target in Central HVAC auto mode."""
        return self._auto_target(heat=False)

    @property
    def target_temperature_high(self) -> float | None:
        """Pre-2026.11 name for native_target_temperature_high."""
        return self.native_target_temperature_high

    @property
    def fan_modes(self) -> list[str]:
        """The AC's fan speeds in the current mode."""
        return (
            self.thermostat.hvac_fan_modes
            if self.thermostat.is_hvac
            else [FAN_NAMES[s] for s in self.thermostat.fan_speeds]
        )

    @property
    def fan_mode(self) -> str | None:
        """The AC's fan speed."""
        if self.thermostat.is_hvac:
            return self.thermostat.hvac_fan_mode
        speed = self.thermostat.fan_speed
        return FAN_NAMES[speed] if speed is not None else None

    @property
    def swing_modes(self) -> list[str]:
        """The AC's vertical swing settings in the current mode."""
        return [SWING_NAMES[s] for s in self.thermostat.swing_options(SWING_VERTICAL)]

    @property
    def swing_mode(self) -> str | None:
        """The AC's vertical swing."""
        value = self.thermostat.swing(SWING_VERTICAL)
        return SWING_NAMES[value] if value is not None else None

    @property
    def swing_horizontal_modes(self) -> list[str]:
        """The AC's horizontal swing settings in the current mode."""
        options = self.thermostat.swing_options(SWING_HORIZONTAL)
        return [SWING_NAMES[s] for s in options]

    @property
    def swing_horizontal_mode(self) -> str | None:
        """The AC's horizontal swing."""
        value = self.thermostat.swing(SWING_HORIZONTAL)
        return SWING_NAMES[value] if value is not None else None

    @property
    def min_temp(self) -> float:
        """Lowest setpoint in the native unit, from the setpoint rules."""
        if self.owner_unit == "F":
            return float(self.thermostat.fahrenheit_setpoint_range[0])
        return self.thermostat.setpoint_range[0]

    @property
    def max_temp(self) -> float:
        """Highest setpoint in the native unit."""
        if self.owner_unit == "F":
            return float(self.thermostat.fahrenheit_setpoint_range[1])
        return self.thermostat.setpoint_range[1]

    @property
    def target_temperature_step(self) -> float:
        """Whole degrees in °F, like the Mysa app; the thermostat's step in °C."""
        return 1.0 if self.owner_unit == "F" else self.thermostat.setpoint_range[2]

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Change the setpoint (and the mode, if given).

        Values arrive in the native unit. The rules clamp a value outside the
        thermostat's range (HA has checked the card's bounds already, so that
        only happens when a mode change narrows them) and log the change. In
        °F, picking the whole °F a target already shows leaves it unchanged,
        and a person's step to the next whole degree always moves the
        setpoint, even when the thermostat's step is coarser (``tap_value``).
        An automation's value goes to its nearest setpoint instead, so one
        that keeps asking for the same target settles on it.
        """
        unit = self.owner_unit
        hvac_mode = kwargs.get(ATTR_HVAC_MODE)
        requested = {
            name: float(kwargs[key])
            for name, key in (
                ("temperature", ATTR_TEMPERATURE),
                ("low", ATTR_TARGET_TEMP_LOW),
                ("high", ATTR_TARGET_TEMP_HIGH),
            )
            if kwargs.get(key) is not None
        }
        new_mode = HVAC_TO_MODE[hvac_mode] if hvac_mode is not None else None
        # Taps and re-picked labels relate to the target of the current mode;
        # a mode change in the same call has no current target to keep.
        tap = new_mode is None or new_mode == self.thermostat.mode
        # Only a person steps with + and -. Home Assistant gives their calls a
        # user and no parent; automations and the scripts they run have neither
        # or a parent.
        context = self._context
        step = (
            tap
            and context is not None
            and context.user_id is not None
            and context.parent_id is None
        )
        async with self._service_call():
            if self.thermostat.basic_cloud_only:
                written = await self.thermostat.async_set_basic_temperature(
                    mode=new_mode,
                    unit=unit,
                    strict=False,
                    tap=tap,
                    step=step,
                    **requested,
                )
            else:
                if new_mode is not None:
                    await self.thermostat.async_set_mode(new_mode)
                written = {}
                if (temperature := requested.get("temperature")) is not None:
                    written[
                        "temperature"
                    ] = await self.thermostat.async_set_temperature(
                        temperature, unit=unit, strict=False, tap=tap, step=step
                    )
        for name, setpoint in written.items():
            if setpoint.clamped:
                self._log_clamp(requested[name], setpoint, unit)

    def _log_clamp(self, requested: float, setpoint: Setpoint, unit: Unit) -> None:
        """Tell the owner, in the logbook, that a target was clamped."""
        assert setpoint.clamp is not None
        limit = "lowest" if setpoint.clamp.bound == "min" else "highest"
        wanted = format_requested(to_celsius(requested, unit), unit)
        actual = format_setpoint(setpoint.celsius, unit)
        message = (
            f"was set to {actual} instead of the requested {wanted}, "
            f"the {limit} setpoint it allows"
        )
        LOGGER.info("%s %s", self.thermostat.name, message)
        self.hass.bus.async_fire(
            EVENT_LOGBOOK_ENTRY,
            {
                "name": self.thermostat.name,
                "message": message,
                "entity_id": self.entity_id,
                "domain": DOMAIN,
            },
            context=self._context,
        )

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Change the mode."""
        async with self._service_call():
            await self.thermostat.async_set_mode(HVAC_TO_MODE[hvac_mode])

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        """Change the AC's fan speed."""
        if self.thermostat.is_hvac:
            await self.thermostat.async_set_hvac_fan_mode(fan_mode)
            return
        await self.thermostat.async_set_fan_speed(FAN_BY_NAME[fan_mode])

    async def async_set_swing_mode(self, swing_mode: str) -> None:
        """Change the AC's vertical swing."""
        await self.thermostat.async_set_swing(SWING_VERTICAL, SWING_BY_NAME[swing_mode])

    async def async_set_swing_horizontal_mode(self, swing_horizontal_mode: str) -> None:
        """Change the AC's horizontal swing."""
        await self.thermostat.async_set_swing(
            SWING_HORIZONTAL, SWING_BY_NAME[swing_horizontal_mode]
        )

    async def async_turn_on(self) -> None:
        """Turn on in the first non-off mode (heat on heaters)."""
        async with self._service_call():
            for mode in self.thermostat.supported_modes:
                if mode != Mode.OFF:
                    await self.thermostat.async_set_mode(mode)
                    return
            if not self.thermostat.basic_cloud_only:
                await self.thermostat.async_set_mode(Mode.HEAT)

    async def async_turn_off(self) -> None:
        """Turn off."""
        async with self._service_call():
            await self.thermostat.async_set_mode(Mode.OFF)

    # ---- entity actions (registered in services.py) --------------------

    async def async_hold(
        self,
        temperature: float | None = None,
        hvac_mode: HVACMode | None = None,
        until: datetime | None = None,
    ) -> None:
        """Hold a setpoint over the Mysa schedule."""
        await async_hold(
            self.hass,
            self.thermostat,
            temperature,
            hvac_mode,
            until,
            unit=self.owner_unit,
        )

    async def async_resume_schedule(self) -> None:
        """End a hold."""
        await async_resume_schedule(self.thermostat)

    async def async_set_schedule(self, events: list[dict[str, Any]]) -> None:
        """Replace the thermostat's weekly schedule."""
        await async_set_schedule(self.thermostat, events, unit=self.owner_unit)

    async def async_clear_schedule(self) -> None:
        """Delete the thermostat's schedule."""
        await async_clear_schedule(self.thermostat)
