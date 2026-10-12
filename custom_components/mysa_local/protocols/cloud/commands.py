"""Semantic Mysa cloud commands and their wire representation.

Home Assistant code should ask for an operation such as setting a temperature.
Only this module translates that operation into Mysa's state/settings fields.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import time
from typing import Any, TypedDict

from .const import (
    AC_FAN_TO_STATE,
    SETPOINT_SECTION_BY_MODE,
    SWING_HORIZONTAL,
    SWING_VERTICAL,
    AcFan,
    ButtonLock,
    HeaterType,
    Mode,
    Swing,
)

StateChanges = dict[str, dict[str, Any]]
SettingsChanges = dict[str, Any]


class ScheduleWireEvent(TypedDict):
    """One schedule event in the cloud's API representation."""

    executionTime: str
    dayOfWeek: str
    clientKey: str
    state: StateChanges


_WEEKDAY_NUMBERS = {
    "sun": 0,
    "mon": 1,
    "tue": 2,
    "wed": 3,
    "thu": 4,
    "fri": 5,
    "sat": 6,
}


@dataclass(frozen=True, slots=True)
class ScheduleEventSpec:
    """A schedule event before encoding it for Mysa's cloud API."""

    days: tuple[str, ...]
    at: time
    mode: Mode
    temperature: float | None
    client_key: str


def temperature_changes(mode: Mode | None, temperature: float) -> StateChanges:
    """Encode a setpoint change for the selected operating mode."""
    section = SETPOINT_SECTION_BY_MODE.get(mode or Mode.HEAT, "targetHeat")
    return {section: {"setpoint": temperature}}


def auto_temperature_changes(low: float, high: float) -> StateChanges:
    """APK 4.20.1 non-legacy AUTO uses a dedicated pair, never targetHeat/Cool."""
    return {"targetAuto": {"heatSetpoint": low, "coolSetpoint": high}}


def hvac_fan_changes(mode: str) -> StateChanges:
    """Basic fan operation, matching the app's always-on/runtime combination."""
    values = {"auto": (0, 0, 1800), "on": (1, 900, 900), "off": (0, 0, 0)}
    if mode not in values:
        raise ValueError(f"Unsupported HVAC fan operation: {mode}")
    always, runtime, period = values[mode]
    return {
        "hvacConfig": {
            "advFanAlwaysOn": always,
            "advFanRuntimeSeconds": runtime,
            "advFanRuntimePeriodSeconds": period,
        }
    }


def mode_changes(mode: Mode) -> StateChanges:
    """Encode an operating mode change."""
    return {"modes": {"mode": int(mode)}}


def make_state(mode: Mode, temperature: float | None) -> StateChanges:
    """Build the state object used by a hold or schedule event."""
    changes: StateChanges = mode_changes(mode)
    section = SETPOINT_SECTION_BY_MODE.get(mode)
    if section is not None and temperature is not None:
        changes[section] = {"setpoint": temperature}
    return changes


def encode_schedule_events(
    events: Iterable[ScheduleEventSpec],
) -> list[dict[str, Any]]:
    """Encode schedule events using Mysa's weekday and state representation."""
    encoded: list[dict[str, Any]] = []
    for event in events:
        for day in event.days:
            try:
                day_number = _WEEKDAY_NUMBERS[day.lower()]
            except KeyError as err:
                raise ValueError(f"Unsupported schedule weekday: {day!r}") from err
            wire_event: ScheduleWireEvent = {
                "executionTime": event.at.strftime("%H:%M:00"),
                "dayOfWeek": str(day_number),
                "clientKey": event.client_key,
                "state": make_state(event.mode, event.temperature),
            }
            encoded.append(dict(wire_event))
    encoded.sort(key=lambda event: (int(event["dayOfWeek"]), event["executionTime"]))
    return encoded


def fan_speed_changes(speed: AcFan, mode: Mode | None = None) -> StateChanges:
    """Encode an AC fan change, including the current mode when known."""
    fields: dict[str, int] = {"fan_mode": AC_FAN_TO_STATE[speed]}
    if mode is not None:
        fields["mode"] = int(mode)
    return {"modes": fields}


def swing_changes(direction: str, value: Swing) -> StateChanges:
    """Encode a vertical or horizontal AC swing change."""
    if direction not in (SWING_VERTICAL, SWING_HORIZONTAL):
        raise ValueError(f"Unsupported swing direction: {direction!r}")
    return {"modes": {f"{direction}State": int(value)}}


def display_unit_settings(unit: str) -> SettingsChanges:
    """Encode the thermostat's display unit setting."""
    normalized = unit.upper()
    if normalized not in ("C", "F"):
        raise ValueError(f"Unsupported display unit: {unit!r}")
    return {"Format": "fahrenheit" if normalized == "F" else "celsius"}


def display_brightness_changes(active: int, idle: int) -> StateChanges:
    """Encode both brightness values, as Mysa's app does in one request."""
    return {"physicalInterface": {"activeIntensity": active, "idleIntensity": idle}}


def button_lock_changes(value: ButtonLock) -> StateChanges:
    """Encode the physical button lock state."""
    return {"physicalInterface": {"lockout": int(value)}}


def setpoint_limit_changes(value: float, *, minimum: bool) -> StateChanges:
    """Encode the minimum or maximum allowed heating setpoint."""
    field = "lockoutMin" if minimum else "lockoutMax"
    return {"targetHeat": {field: value}}


def temperature_offset_changes(value: float) -> StateChanges:
    """Encode the ambient temperature offset."""
    return {"tracking": {"ambientOffset": value}}


def heater_type_settings(heater_type: int | HeaterType) -> SettingsChanges:
    """Encode the heater profile in the device settings record."""
    return {"DutyCycleOpt": int(heater_type)}
