"""Typed views of Mysa cloud responses.

Every model keeps the raw JSON in `raw`, so new fields can be read without a
library change. Parsing is lenient: a missing or oddly typed field becomes None.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
import itertools
from typing import Any

from .const import MODE_BY_NAME, SETPOINT_SECTION_BY_MODE, ChangeSource, Mode


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _float(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return int(value)


def _str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _time(value: Any) -> datetime | None:
    seconds = _float(value)
    if seconds is None or seconds <= 0:
        return None
    return datetime.fromtimestamp(seconds, UTC)


def _section(data: Mapping[str, Any], name: str) -> dict[str, Any]:
    """Return a state section, with confirmed (reported) values over desired ones."""
    section = _dict(data.get(name))
    merged = dict(_dict(section.get("desired")))
    merged.update(_dict(section.get("reported")))
    if not merged:
        # Sections such as drState aren't split into desired/reported.
        merged = {k: v for k, v in section.items() if k not in ("desired", "reported")}
    return merged


def value_range(values: Iterable[Any]) -> tuple[float, float, float] | None:
    """Turn a capability validValues list into (min, max, step)."""
    numbers = sorted({v for v in (_float(x) for x in values) if v is not None})
    if not numbers:
        return None
    steps = [b - a for a, b in itertools.pairwise(numbers) if b > a]
    step = round(min(steps), 4) if steps else 1.0
    return numbers[0], numbers[-1], step


@dataclass(frozen=True, slots=True)
class DeviceState:
    """One thermostat's state from /state/batch or /state/{dev}."""

    device_id: str
    raw: dict[str, Any] = field(repr=False)
    connected: bool | None
    last_connected: datetime | None
    reading_time: datetime | None
    room_temperature: float | None
    humidity: float | None
    rssi: int | None
    duty_cycle: float | None
    mode: Mode | None
    heat_setpoint: float | None
    cool_setpoint: float | None
    setpoint_min: float | None
    setpoint_max: float | None
    button_lock: int | None
    active_brightness: int | None
    idle_brightness: int | None
    display_unit: str | None
    temperature_offset: float | None
    control_type: int | None
    model: str | None
    firmware: str | None
    in_demand_response: bool | None

    @classmethod
    def from_api(cls, device_id: str, data: Mapping[str, Any]) -> DeviceState:
        """Parse the `data` object of a state response."""
        telemetry = _dict(data.get("latestTelemetry"))
        reading = _dict(telemetry.get("reading"))
        heat = _section(data, "targetHeat")
        cool = _section(data, "targetCool")
        modes = _section(data, "modes")
        ui = _section(data, "physicalInterface")
        identity = _section(data, "identity")
        dr_state = _section(data, "drState")
        raw_mode = _int(modes.get("mode"))
        try:
            mode = Mode(raw_mode) if raw_mode is not None else None
        except ValueError:
            mode = None
        connected = telemetry.get("isConnected")
        in_event = dr_state.get("inEvent")
        return cls(
            device_id=device_id,
            raw=dict(data),
            connected=connected if isinstance(connected, bool) else None,
            last_connected=_time(telemetry.get("lastConnected")),
            reading_time=_time(reading.get("timestamp")),
            room_temperature=_float(reading.get("roomTemperature")),
            humidity=_float(reading.get("humidity")),
            rssi=_int(reading.get("rssi")),
            duty_cycle=_float(reading.get("dutyCycle")),
            mode=mode,
            heat_setpoint=_float(heat.get("setpoint")),
            cool_setpoint=_float(cool.get("setpoint")),
            setpoint_min=_float(heat.get("lockoutMin")),
            setpoint_max=_float(heat.get("lockoutMax")),
            button_lock=_int(ui.get("lockout")),
            active_brightness=_int(ui.get("activeIntensity")),
            idle_brightness=_int(ui.get("idleIntensity")),
            display_unit=_str(ui.get("format")),
            temperature_offset=_float(_section(data, "tracking").get("ambientOffset")),
            control_type=_int(_section(data, "bbConfig").get("controlType")),
            model=_str(identity.get("model")),
            firmware=_str(identity.get("fw")),
            in_demand_response=in_event if isinstance(in_event, bool) else None,
        )

    @property
    def heating(self) -> bool | None:
        """True while the heater relay is on."""
        if self.duty_cycle is None:
            return None
        return self.duty_cycle > 0

    def setpoint_for(self, mode: Mode | None) -> float | None:
        """The setpoint that applies in a mode (heat setpoint by default)."""
        if mode is not None and SETPOINT_SECTION_BY_MODE.get(mode) == "targetCool":
            return self.cool_setpoint
        return self.heat_setpoint


@dataclass(frozen=True, slots=True)
class Capabilities:
    """What a model supports, from GET /capabilities/{dev}."""

    raw: dict[str, Any] = field(repr=False)
    modes: tuple[Mode, ...]
    heat_setpoint_range: tuple[float, float, float] | None
    cool_setpoint_range: tuple[float, float, float] | None
    control_types: tuple[int, ...]
    offset_range: tuple[float, float, float] | None
    lockout_values: tuple[int, ...]
    brightness: bool
    units: tuple[str, ...]
    humidity_sensor: bool
    homekit: bool
    matter: bool
    scheduling: bool
    model: str | None
    firmware: str | None

    @classmethod
    def from_api(cls, data: Mapping[str, Any]) -> Capabilities:
        """Parse a capability document."""
        features = _dict(data.get("features"))
        climate = _dict(features.get("climateControl"))
        sensing = _dict(features.get("sensing"))
        interface = _dict(features.get("interface"))
        connectivity = _dict(features.get("connectivity"))
        smart = _dict(features.get("smart"))
        sensors = _dict(_dict(data.get("hardware")).get("sensors"))
        system = _dict(data.get("system"))

        def controllable(node: Any) -> dict[str, Any]:
            node = _dict(node)
            return node if node.get("userControllable", True) else {}

        def valid(node: Any) -> list[Any]:
            values = controllable(node).get("validValues")
            return values if isinstance(values, list) else []

        modes = tuple(
            MODE_BY_NAME[name]
            for name in valid(climate.get("mode"))
            if isinstance(name, str) and name in MODE_BY_NAME
        )
        control_type = _dict(
            _dict(_dict(climate.get("advancedConfig")).get("baseboardHeating")).get(
                "controlType"
            )
        )
        offset = _dict(_dict(sensing.get("temperature")).get("temperatureOffset"))
        return cls(
            raw=dict(data),
            modes=modes,
            heat_setpoint_range=value_range(
                valid(_dict(climate.get("heat")).get("setpoint"))
            ),
            cool_setpoint_range=value_range(
                valid(_dict(climate.get("cool")).get("setpoint"))
            ),
            control_types=tuple(
                v for v in (_int(x) for x in valid(control_type)) if v is not None
            ),
            offset_range=value_range(valid(offset)),
            lockout_values=tuple(
                v
                for v in (_int(x) for x in valid(interface.get("lockout")))
                if v is not None
            ),
            brightness=bool(controllable(interface.get("brightness"))),
            units=tuple(v for v in valid(interface.get("unit")) if isinstance(v, str)),
            humidity_sensor=sensors.get("humiditySensor") is True,
            homekit=connectivity.get("homekit") is True,
            matter=connectivity.get("matter") is True,
            scheduling=smart.get("scheduling") is True,
            model=_str(system.get("model")),
            firmware=_str(system.get("firmwareVersion")),
        )


@dataclass(frozen=True, slots=True)
class DeviceRecord:
    """A thermostat's settings record (GET /devices or bootstrap)."""

    device_id: str
    raw: dict[str, Any] = field(repr=False)
    name: str | None
    model: str | None
    home_id: str | None
    time_zone: str | None
    display_format: str | None
    duty_cycle_opt: int | None

    @classmethod
    def from_api(cls, device_id: str, data: Mapping[str, Any]) -> DeviceRecord:
        """Parse one entry of DevicesObj."""
        return cls(
            device_id=device_id,
            raw=dict(data),
            name=_str(data.get("Name")),
            model=_str(data.get("Model")),
            home_id=_str(data.get("Home")),
            time_zone=_str(data.get("TimeZone")),
            display_format=_str(data.get("Format")),
            duty_cycle_opt=_int(data.get("DutyCycleOpt")),
        )

    @property
    def display_unit(self) -> str | None:
        """C or F, from the record's 'celsius'/'fahrenheit'."""
        if self.display_format is None:
            return None
        return "F" if self.display_format.lower().startswith("f") else "C"


@dataclass(frozen=True, slots=True)
class FirmwareInfo:
    """GET /devices/update_available/{dev}."""

    update_available: bool
    installed_version: str | None
    allowed_version: str | None

    @classmethod
    def from_api(cls, data: Mapping[str, Any]) -> FirmwareInfo:
        """Parse the response. `update` may be false or a truthy value."""
        return cls(
            update_available=bool(data.get("update")),
            installed_version=_str(data.get("installedVersion")),
            allowed_version=_str(data.get("allowedVersion")),
        )


@dataclass(frozen=True, slots=True)
class UsageBucket:
    """30-minute usage bucket."""

    start: datetime
    heat_seconds: float | None
    temperature: float | None


@dataclass(frozen=True, slots=True)
class UsageDay:
    """One local day of POST /telemetry/usage/{dev}."""

    start: datetime
    heat_seconds: float | None
    average_temperature: float | None
    buckets: tuple[UsageBucket, ...]

    @classmethod
    def from_api(cls, data: Mapping[str, Any]) -> UsageDay | None:
        """Parse one day; None if it has no start time."""
        start = _time(data.get("T"))
        if start is None:
            return None
        summary = _dict(_dict(data.get("summary")).get("a"))
        buckets = []
        for raw_item in data.get("aggregatedReadings") or []:
            item = _dict(raw_item)
            bucket_start = _time(item.get("T"))
            if bucket_start is None:
                continue
            values = _dict(item.get("a"))
            buckets.append(
                UsageBucket(
                    start=bucket_start,
                    heat_seconds=_float(values.get("oH1")),
                    temperature=_float(values.get("t")),
                )
            )
        return cls(
            start=start,
            heat_seconds=_float(summary.get("oH1")),
            average_temperature=_float(summary.get("t")),
            buckets=tuple(buckets),
        )


@dataclass(frozen=True, slots=True)
class StateChange:
    """One entry of GET /state/history/{dev}."""

    field: str
    time: datetime
    value: Any
    source: str | None

    @property
    def from_device(self) -> bool:
        """True if someone pressed the thermostat's own buttons."""
        return self.source == ChangeSource.DEVICE


@dataclass(frozen=True, slots=True)
class Bootstrap:
    """GET /mobile/bootstrap: who the user is and what they can see."""

    user_id: str | None
    home_ids: tuple[str, ...]
    devices: dict[str, DeviceRecord]

    @classmethod
    def from_api(cls, data: Mapping[str, Any]) -> Bootstrap:
        """Parse the bootstrap response."""
        body = _dict(data.get("data"))
        user = _dict(body.get("user"))
        homes = user.get("AllowedHomes")
        devices = {
            dev_id: DeviceRecord.from_api(dev_id, _dict(record))
            for dev_id, record in _dict(body.get("devices")).items()
        }
        return cls(
            user_id=_str(user.get("Id")),
            home_ids=tuple(h for h in homes if isinstance(h, str))
            if isinstance(homes, list)
            else (),
            devices=devices,
        )
