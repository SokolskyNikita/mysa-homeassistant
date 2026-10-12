"""Conservative validation for experimental cloud-only models."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .const import Mode, ModelFamily
from .models import Capabilities, DeviceState
from .observations import number


def validate_basic_command(
    family: ModelFamily,
    caps: Capabilities | None,
    state: DeviceState | None,
    expected: dict[str, Any],
    command_value: Callable[[str, Any], Any],
) -> None:
    """Reject unsupported or incomplete operations before issuing a cloud write."""
    if caps is None:
        raise ValueError("Device capabilities are unavailable; retry after a refresh")
    if family == ModelFamily.CLIMATE_SENSOR:
        raise ValueError("Climate Sensor supports readings only")
    supported = caps.basic_modes(family)
    if state and state.auto_enabled == 0:
        supported = [m for m in supported if m != Mode.AUTO]
    mode = expected.get("mode", command_value("mode", state.mode if state else None))
    if "mode" in expected and mode not in supported:
        raise ValueError("Operating mode is not supported by this device")
    ranges = {
        "heat_setpoint": (caps.heat_setpoint_range, Mode.HEAT),
        "cool_setpoint": (caps.cool_setpoint_range, Mode.COOL),
        "auto_heat_setpoint": (caps.auto_heat_range, Mode.AUTO),
        "auto_cool_setpoint": (caps.auto_cool_range, Mode.AUTO),
    }
    for name, (limits, required_mode) in ranges.items():
        if name not in expected:
            continue
        value = number(expected[name])
        if (
            required_mode not in supported
            or limits is None
            or value is None
            or not limits[0] <= value <= limits[1]
        ):
            raise ValueError(f"Unsupported or out-of-range target: {name}")
        if required_mode == Mode.AUTO and mode != Mode.AUTO:
            raise ValueError("Dual setpoints require heat/cool mode")
        if required_mode != Mode.AUTO and mode not in (required_mode, Mode.OFF):
            raise ValueError("Setpoint does not match the selected mode")
        if state:
            low = (
                state.cool_min
                if required_mode == Mode.COOL or name == "auto_cool_setpoint"
                else state.setpoint_min
            )
            high = (
                state.cool_max
                if required_mode == Mode.COOL or name == "auto_cool_setpoint"
                else state.setpoint_max
            )
            if (low is not None and value < low) or (high is not None and value > high):
                raise ValueError("Target exceeds known device limits")
    if "auto_heat_setpoint" in expected or "auto_cool_setpoint" in expected:
        low = number(expected.get("auto_heat_setpoint"))
        high = number(expected.get("auto_cool_setpoint"))
        delta = max(
            caps.min_setpoint_delta or 0, state.auto_deadband or 0 if state else 0
        )
        if low is None or high is None or high <= low or high - low < delta - 1e-6:
            raise ValueError(
                "Both auto setpoints must be supplied in order, with the required minimum gap"
            )
    if "fan_mode" in expected and (
        family != ModelFamily.CENTRAL_HVAC
        or expected["fan_mode"] not in caps.hvac_fan_speeds(mode)
    ):
        raise ValueError("Fan speed is not supported in this mode")
    if any(
        name in expected for name in ("fan_always_on", "fan_runtime", "fan_period")
    ) and (family != ModelFamily.CENTRAL_HVAC or not caps.hvac_fan_control):
        raise ValueError("Fan operation is not supported by this device")
    allowed = {
        *ranges,
        "mode",
        "fan_mode",
        "fan_always_on",
        "fan_runtime",
        "fan_period",
        "display_unit",
        "button_lock",
        "active_brightness",
        "idle_brightness",
    }
    if expected.keys() - allowed:
        raise ValueError("This setting is not implemented for this model")
