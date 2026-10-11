"""Actions: holds and on-thermostat schedules."""

from __future__ import annotations

from homeassistant.components.climate.const import (
    ATTR_HVAC_MODE,
    DOMAIN as CLIMATE_DOMAIN,
    HVACMode,
)
from homeassistant.const import ATTR_TEMPERATURE
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import config_validation as cv, service
from homeassistant.helpers.typing import VolDictType
import voluptuous as vol

from .const import ATTR_EVENTS, ATTR_UNTIL, DOMAIN

WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")

HOLD_SCHEMA: VolDictType = {
    vol.Optional(ATTR_TEMPERATURE): vol.Coerce(float),
    vol.Optional(ATTR_HVAC_MODE): vol.Coerce(HVACMode),
    vol.Optional(ATTR_UNTIL): cv.datetime,
}

EVENT_SCHEMA = vol.Schema(
    {
        vol.Required("days"): vol.All(cv.ensure_list, [vol.In(WEEKDAYS)]),
        vol.Required("time"): cv.time,
        vol.Optional(ATTR_TEMPERATURE): vol.Coerce(float),
        vol.Optional(ATTR_HVAC_MODE, default=HVACMode.HEAT): vol.In(
            [HVACMode.HEAT, HVACMode.COOL, HVACMode.OFF]
        ),
    }
)
SCHEDULE_SCHEMA: VolDictType = {
    vol.Required(ATTR_EVENTS): vol.All(
        cv.ensure_list, [EVENT_SCHEMA], vol.Length(min=1)
    ),
}


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the entity actions on Mysa climate entities."""
    actions: tuple[tuple[str, VolDictType | None, str], ...] = (
        ("hold", HOLD_SCHEMA, "async_hold"),
        ("resume_schedule", None, "async_resume_schedule"),
        ("set_schedule", SCHEDULE_SCHEMA, "async_set_schedule"),
        ("clear_schedule", None, "async_clear_schedule"),
    )
    for name, schema, method in actions:
        service.async_register_platform_entity_service(
            hass,
            DOMAIN,
            name,
            entity_domain=CLIMATE_DOMAIN,
            schema=schema,
            func=method,
        )
