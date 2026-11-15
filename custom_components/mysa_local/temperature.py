"""The owner's temperature unit, and setpoints shown and worded in it.

Mysa stores every temperature in °C on a 0.5 °C grid; ``SetpointRules`` in
mysa-client rounds and checks what is written. This module only decides how
those values look to a Home Assistant user: whole °F (rounded halfway up, like
the Mysa app) for Fahrenheit owners, the °C value otherwise.
"""

from __future__ import annotations

from typing import Literal

from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from mysa_client.cloud import (
    SetpointError,
    SetpointGapTooSmallError,
    SetpointLimitOrderError,
    SetpointModeError,
    SetpointOutOfRangeError,
    fahrenheit_label,
)
from mysa_client.cloud.setpoints import round_half_up

from .const import DOMAIN

Unit = Literal["C", "F"]


def user_unit(hass: HomeAssistant) -> Unit:
    """F when Home Assistant's unit system uses Fahrenheit, else C."""
    return (
        "F"
        if hass.config.units.temperature_unit == UnitOfTemperature.FAHRENHEIT
        else "C"
    )


def ha_unit(unit: Unit) -> UnitOfTemperature:
    """Home Assistant's name for a unit."""
    return UnitOfTemperature.FAHRENHEIT if unit == "F" else UnitOfTemperature.CELSIUS


def half_up(value: float, digits: int = 1) -> float:
    """Round to ``digits`` decimals with halfway values up (21.25 -> 21.3)."""
    scale: float = 10.0**digits
    return float(round_half_up(value * scale)) / scale


def display_setpoint(celsius: float | None, unit: Unit) -> float | None:
    """A setpoint as the owner sees it: whole °F, or the °C value."""
    if celsius is None:
        return None
    # An int, so a whole °F shows as "73" rather than "73.0".
    return fahrenheit_label(celsius) if unit == "F" else celsius


def display_reading(celsius: float | None, unit: Unit) -> float | None:
    """A measured temperature in the owner's unit, to one decimal in °F."""
    if celsius is None:
        return None
    return half_up(celsius * 1.8 + 32) if unit == "F" else celsius


def _number(value: float) -> str:
    return f"{half_up(value):g}"


def format_setpoint(celsius: float, unit: Unit) -> str:
    """A rounded setpoint for messages, e.g. ``73 °F`` or ``22.5 °C``."""
    if unit == "F":
        return f"{fahrenheit_label(celsius)} °F"
    return f"{_number(celsius)} °C"


def format_requested(celsius: float, unit: Unit) -> str:
    """A requested (unrounded) value for messages, back in the owner's unit."""
    value = celsius * 1.8 + 32 if unit == "F" else celsius
    return f"{_number(value)} °{unit}"


def format_delta(delta_c: float, unit: Unit) -> str:
    """A temperature difference for messages, e.g. ``1.8 °F``."""
    value = delta_c * 1.8 if unit == "F" else delta_c
    return f"{_number(value)} °{unit}"


def _error(key: str, **placeholders: str) -> ServiceValidationError:
    return ServiceValidationError(
        translation_domain=DOMAIN,
        translation_key=key,
        translation_placeholders=placeholders or None,
    )


def setpoint_error(err: SetpointError, unit: Unit) -> ServiceValidationError:
    """Word a rules error in the owner's unit.

    In °F an out-of-range limit is the closest whole °F that is allowed
    (``err.fahrenheit_limit``), so the owner can pick it.
    """
    if isinstance(err, SetpointGapTooSmallError):
        return _error(
            "setpoint_gap_too_small",
            low=format_setpoint(err.low, unit),
            high=format_setpoint(err.high, unit),
            required=format_delta(err.required, unit),
        )
    if isinstance(err, SetpointLimitOrderError):
        return _error("limits_invalid")
    if isinstance(err, SetpointModeError):
        return _error("setpoint_mode_unsupported")
    if isinstance(err, SetpointOutOfRangeError):
        fmt = format_delta if err.delta else format_requested
        whole = err.fahrenheit_limit if unit == "F" else None
        limit = f"{whole} °F" if whole is not None else fmt(err.limit, unit)
        # A heat/cool pair names the side that was refused.
        prefix = f"{err.side}_" if err.side else ""
        bound = "below_minimum" if err.bound == "min" else "above_maximum"
        return _error(
            f"{prefix}setpoint_{bound}",
            requested=fmt(err.requested, unit),
            limit=limit,
        )
    return ServiceValidationError(str(err))
