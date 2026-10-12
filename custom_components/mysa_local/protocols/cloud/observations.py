"""Normalized observations with independent clocks and provenance."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import math
from typing import Any

FUTURE_TOLERANCE = timedelta(minutes=1)
MEASUREMENT_MAX_AGE = timedelta(minutes=35)
MEASUREMENTS = frozenset(
    {"room_temperature", "humidity", "duty_cycle", "rssi", "hvac_state", "hvac_stages"}
)
STATE_FIELDS = {
    "auto_heat_setpoint": ("targetAuto", "heatSetpoint"),
    "auto_cool_setpoint": ("targetAuto", "coolSetpoint"),
    "auto_enabled": ("targetAuto", "enabled"),
    "auto_deadband": ("targetAuto", "deadband"),
    "cool_min": ("targetCool", "lockoutMin"),
    "cool_max": ("targetCool", "lockoutMax"),
    "hvac_stages": ("modes", "hvacStates"),
    "fan_always_on": ("hvacConfig", "advFanAlwaysOn"),
    "fan_runtime": ("hvacConfig", "advFanRuntimeSeconds"),
    "fan_period": ("hvacConfig", "advFanRuntimePeriodSeconds"),
    "heat_setpoint": ("targetHeat", "setpoint"),
    "cool_setpoint": ("targetCool", "setpoint"),
    "setpoint_min": ("targetHeat", "lockoutMin"),
    "setpoint_max": ("targetHeat", "lockoutMax"),
    "mode": ("modes", "mode"),
    "fan_mode": ("modes", "fan_mode"),
    "vertical_swing": ("modes", "verticalSwingState"),
    "horizontal_swing": ("modes", "horizontalSwingState"),
    "button_lock": ("physicalInterface", "lockout"),
    "active_brightness": ("physicalInterface", "activeIntensity"),
    "idle_brightness": ("physicalInterface", "idleIntensity"),
    "display_unit": ("physicalInterface", "format"),
    "temperature_offset": ("tracking", "ambientOffset"),
    "control_type": ("bbConfig", "controlType"),
    "model": ("identity", "model"),
    "firmware": ("identity", "fw"),
    "in_demand_response": ("drState", "inEvent"),
}
TELEMETRY_FIELDS = {
    "hvac_state": "hvacState",
    "room_temperature": "roomTemperature",
    "humidity": "humidity",
    "duty_cycle": "dutyCycle",
    "rssi": "rssi",
}


def number(value: Any) -> float | None:
    """Finite JSON numbers, excluding booleans."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    try:
        result = float(value)
    except OverflowError:
        return None
    return result if math.isfinite(result) else None


def timestamp(value: Any) -> datetime | None:
    """Parse an aware ISO clock or Unix seconds/milliseconds without raising."""
    try:
        if isinstance(value, str):
            result = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return result.astimezone(UTC) if result.tzinfo else None
        seconds = number(value)
        if seconds is None or seconds <= 0:
            return None
        if seconds >= 100_000_000_000:
            seconds /= 1000
        return datetime.fromtimestamp(seconds, UTC)
    except ValueError, OverflowError, OSError:
        return None


def valid_value(field: str, value: Any) -> bool:
    """Reject values outside the semantics of the normalized field."""
    if value is None:
        return False
    if field in (
        "room_temperature",
        "heat_setpoint",
        "cool_setpoint",
        "auto_heat_setpoint",
        "auto_cool_setpoint",
        "cool_min",
        "cool_max",
        "setpoint_min",
        "setpoint_max",
        "temperature_offset",
    ):
        return number(value) is not None and -100 <= value <= 100
    if field == "hvac_state":
        return number(value) is not None and int(value) == value and 0 <= value <= 65535
    if field == "hvac_stages":
        keys = (
            "HeatingStage1",
            "HeatingStage2",
            "CoolingStage1",
            "CoolingStage2",
            "AuxHeat",
        )
        return isinstance(value, dict) and all(
            value.get(k) in (0, 1, False, True) for k in keys
        )
    if field == "fan_always_on":
        return value in (0, 1) and not isinstance(value, bool)
    if field in ("fan_runtime", "fan_period", "auto_deadband"):
        return number(value) is not None and value >= 0
    if field == "auto_enabled":
        return isinstance(value, bool) or (type(value) is int and value in (0, 1))
    if field == "humidity" or field in ("active_brightness", "idle_brightness"):
        return number(value) is not None and 0 <= value <= 100
    if field == "duty_cycle":
        return number(value) is not None and 0 <= value <= 1
    if field == "display_unit":
        return value in ("C", "F")
    if field == "button_lock":
        return value in (0, 3) and not isinstance(value, bool)
    return True


@dataclass(frozen=True, slots=True)
class Observation:
    """A source value; fetch time is never substituted for measurement time."""

    value: Any
    source: str
    observed_at: datetime | None
    fetched_at: datetime
    kind: str = "reported"
    clock_valid: bool = True

    def usable(self, now: datetime) -> bool:
        """Whether the clock is plausible, independently of age."""
        return self.clock_valid and (
            self.observed_at is None or self.observed_at <= now + FUTURE_TOLERANCE
        )

    def fresh(self, now: datetime, max_age: timedelta = MEASUREMENT_MAX_AGE) -> bool:
        """A measurement requires its own plausible clock."""
        return (
            self.usable(now)
            and self.observed_at is not None
            and now - self.observed_at <= max_age
        )


def observe(
    value: Any, source: str, clock: Any, now: datetime, *, kind: str = "reported"
) -> Observation:
    """Keep malformed clocks distinct from missing clocks."""
    parsed = timestamp(clock)
    return Observation(
        value, source, parsed, now, kind, clock is None or parsed is not None
    )
