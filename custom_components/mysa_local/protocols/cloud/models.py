"""Typed views of Mysa cloud responses.

Every model keeps the raw JSON in `raw`, so new fields can be read without a
library change. Parsing is lenient: a missing or oddly typed field becomes None.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
import itertools
from typing import Any

from .const import (
    AC_FAN_KEYS,
    AC_FAN_TO_STATE,
    AC_MODE_KEYS,
    AC_SWING_KEYS,
    MODE_BY_NAME,
    SETPOINT_SECTION_BY_MODE,
    STATE_TO_AC_FAN,
    AcFan,
    ChangeSource,
    Mode,
    ModelFamily,
    Swing,
    model_family,
)
from .observations import (
    STATE_FIELDS,
    TELEMETRY_FIELDS,
    Observation,
    number,
    observe,
    timestamp,
    valid_value,
)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _float(value: Any) -> float | None:
    return number(value)


def _int(value: Any) -> int | None:
    numeric = number(value)
    return int(numeric) if numeric is not None and numeric.is_integer() else None


def _str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _ints(value: Any) -> list[int]:
    """Whole numbers from a JSON list (numeric strings count too)."""
    if not isinstance(value, list):
        return []
    numbers = []
    for item in value:
        if isinstance(item, str) and item.strip().isdigit():
            numbers.append(int(item))
        elif (number := _int(item)) is not None:
            numbers.append(number)
    return numbers


def _time(value: Any) -> datetime | None:
    return timestamp(value)


def _section(data: Mapping[str, Any], name: str) -> dict[str, Any]:
    """Return a state section's confirmed (reported) values.

    A value only in `desired` was asked for but never confirmed: a field the
    model doesn't support, or a thermostat that has been offline since. It
    isn't the thermostat's state.
    """
    section = _dict(data.get(name))
    if "desired" in section or "reported" in section:
        return dict(_dict(section.get("reported")))
    # Sections such as drState aren't split into desired/reported.
    return dict(section)


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
    # modes.fan_mode as stored; a Mini-Split AC numbers it as in AC_FAN_TO_STATE.
    fan_mode: int | None
    vertical_swing: int | None
    horizontal_swing: int | None
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
    observations: dict[str, Observation] = field(default_factory=dict)
    reported_observations: dict[str, Observation] = field(default_factory=dict)

    auto_heat_setpoint: float | None = None
    auto_cool_setpoint: float | None = None
    auto_enabled: bool | int | None = None
    auto_deadband: float | None = None
    cool_min: float | None = None
    cool_max: float | None = None
    hvac_state: int | None = None
    hvac_stages: dict[str, Any] | None = None
    fan_always_on: int | None = None
    fan_runtime: float | None = None
    fan_period: float | None = None

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
        result = cls(
            device_id=device_id,
            auto_heat_setpoint=_float(_section(data, "targetAuto").get("heatSetpoint")),
            auto_cool_setpoint=_float(_section(data, "targetAuto").get("coolSetpoint")),
            auto_enabled=_section(data, "targetAuto").get("enabled"),
            auto_deadband=_float(_section(data, "targetAuto").get("deadband")),
            cool_min=_float(cool.get("lockoutMin")),
            cool_max=_float(cool.get("lockoutMax")),
            hvac_state=_int(reading.get("hvacState")),
            hvac_stages=_dict(modes.get("hvacStates")) or None,
            fan_always_on=_int(_section(data, "hvacConfig").get("advFanAlwaysOn")),
            fan_runtime=_float(
                _section(data, "hvacConfig").get("advFanRuntimeSeconds")
            ),
            fan_period=_float(
                _section(data, "hvacConfig").get("advFanRuntimePeriodSeconds")
            ),
            raw=dict(data),
            connected=connected if isinstance(connected, bool) else None,
            last_connected=_time(telemetry.get("lastConnected")),
            reading_time=_time(reading.get("timestamp")),
            room_temperature=_float(reading.get("roomTemperature")),
            humidity=_float(reading.get("humidity")),
            rssi=_int(reading.get("rssi")),
            duty_cycle=_float(reading.get("dutyCycle")),
            mode=mode,
            fan_mode=_int(modes.get("fan_mode")),
            vertical_swing=_int(modes.get("verticalSwingState")),
            horizontal_swing=_int(modes.get("horizontalSwingState")),
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

        now = datetime.now(UTC)
        observations = {}
        for name in TELEMETRY_FIELDS:
            if valid_value(name, value := getattr(result, name)):
                observations[name] = observe(value, "S", reading.get("timestamp"), now)
        for name, (section, _) in STATE_FIELDS.items():
            if valid_value(name, value := getattr(result, name)):
                observations[name] = observe(
                    value, "S", _section(data, section).get("timestamp"), now
                )
        if result.connected is not None:
            observations["connected"] = observe(
                result.connected, "S", telemetry.get("lastConnected"), now
            )
        # Invalid fields do not poison the rest of the device.
        fields: dict[str, Any] = {
            name: obs.value if obs.usable(now) else None
            for name, obs in observations.items()
        }
        for name in (*TELEMETRY_FIELDS, *STATE_FIELDS):
            fields.setdefault(name, None)
        return replace(
            result,
            observations=observations,
            reported_observations=observations,
            **fields,
        )

    @classmethod
    def from_legacy(
        cls,
        device_id: str,
        data: Mapping[str, Any],
        *,
        source: str = "R",
        model: str | None = None,
    ) -> DeviceState:
        """Decode legacy per-field clocks and enums separately from structured state."""
        result = cls.from_api(device_id, {})
        now = datetime.now(UTC)
        observations: dict[str, Observation] = {}
        mapping = {
            "CorrectedTemp": "room_temperature",
            "Humidity": "humidity",
            "SetPoint": "heat_setpoint",
            "Duty": "duty_cycle",
            "Rssi": "rssi",
            "Connected": "connected",
            "Lock": "button_lock",
            "TstatMode": "mode",
        }
        for wire, name in mapping.items():
            item = _dict(data.get(wire))
            value = item.get("v")
            numeric = _int(value)
            if name == "mode":
                # TstatMode uses the legacy control enum; Mode is a distinct
                # operating-state enum and cannot be substituted here.
                modes = {1: Mode.OFF, 3: Mode.HEAT}
                family = model_family(model) if model else None
                if family == ModelFamily.AC:
                    modes.update(
                        {2: Mode.AUTO, 4: Mode.COOL, 5: Mode.FAN_ONLY, 6: Mode.DRY}
                    )
                elif family == ModelFamily.INFLOOR:
                    modes[5] = Mode.HEAT
                value = modes.get(numeric if numeric is not None else -1)
            elif name == "button_lock":
                value = {0: 0, 1: 3}.get(numeric if numeric is not None else -1)
            elif name == "connected":
                value = value if isinstance(value, bool) else None
            else:
                value = _int(value) if name == "rssi" else _float(value)
            if valid_value(name, value):
                observations[name] = observe(value, source, item.get("t"), now)
        values = {
            name: obs.value for name, obs in observations.items() if obs.usable(now)
        }
        reading = observations.get("room_temperature")
        connected = observations.get("connected")
        return replace(
            result,
            raw=dict(data),
            observations=observations,
            reported_observations=observations,
            reading_time=reading.observed_at
            if reading and reading.usable(now)
            else None,
            last_connected=connected.observed_at if connected else None,
            **values,
        )

    @property
    def heating(self) -> bool | None:
        """Whether the last telemetry interval recorded heating/cooling activity."""
        if self.duty_cycle is None:
            return None
        return self.duty_cycle > 0

    def setpoint_for(self, mode: Mode | None) -> float | None:
        """The setpoint that applies in a mode (heat setpoint by default)."""
        if mode == Mode.AUTO:
            return None
        if mode is not None and SETPOINT_SECTION_BY_MODE.get(mode) == "targetCool":
            return self.cool_setpoint
        return self.heat_setpoint

    @property
    def ac_fan(self) -> AcFan | None:
        """The AC fan speed in Mysa's app enum, if the state has one."""
        return STATE_TO_AC_FAN.get(self.fan_mode) if self.fan_mode is not None else None


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

    auto_heat_range: tuple[float, float, float] | None = None
    auto_cool_range: tuple[float, float, float] | None = None
    min_setpoint_delta: float | None = None
    auto_enabled: bool | None = None

    def fan_values(self, mode: Mode | None, field: str) -> tuple[Any, ...]:
        """Mode-specific native HVAC fan capabilities (not AC remote enums)."""
        climate = _dict(_dict(self.raw.get("features")).get("climateControl"))
        name = next((k for k, v in MODE_BY_NAME.items() if v == mode), "")
        node = _dict(climate.get(name))
        if mode not in (Mode.OFF, Mode.FAN_ONLY):
            node = _dict(node.get("fan"))
        capability = _dict(node.get(field))
        values = capability.get("validValues")
        return (
            tuple(values)
            if isinstance(values, list) and capability.get("userControllable", True)
            else ()
        )

    def hvac_fan_speeds(self, mode: Mode | None) -> tuple[int, ...]:
        """Native speed enum; auto=9, unlike Mini-Split AC auto=0."""
        modes = self.fan_values(mode, "fanMode")
        values = _ints(list(self.fan_values(mode, "fanSpeed")))
        return tuple(
            v
            for v in values
            if (v == 9 and "auto" in modes) or (0 <= v <= 8 and "manual" in modes)
        )

    @property
    def hvac_fan_control(self) -> bool:
        """Runtime fan operation is advertised by HVAC's fan period capability."""
        climate = _dict(_dict(self.raw.get("features")).get("climateControl"))
        misc = _dict(_dict(climate.get("advancedConfig")).get("misc"))
        node = _dict(misc.get("fanRuntimePeriod"))
        return bool(node) and node.get("userControllable", True) is True

    @property
    def hvac_fan_operations(self) -> tuple[str, ...]:
        """Basic app fan operations whose periods fit the device capability."""
        if not self.hvac_fan_control:
            return ()
        climate = _dict(_dict(self.raw.get("features")).get("climateControl"))
        misc = _dict(_dict(climate.get("advancedConfig")).get("misc"))
        period = _dict(misc.get("fanRuntimePeriod"))
        low, high = _float(period.get("minValue")), _float(period.get("maxValue"))
        return tuple(
            name
            for name, seconds in (("auto", 1800), ("on", 900), ("off", 0))
            if (low is None or seconds >= low) and (high is None or seconds <= high)
        )

    def basic_modes(self, family: ModelFamily | None) -> list[Mode]:
        """Only expose implemented controls with usable capability limits."""
        if family == ModelFamily.CLIMATE_SENSOR:
            return []
        allowed = {Mode.OFF}
        if self.heat_setpoint_range:
            allowed.add(Mode.HEAT)
        if family == ModelFamily.CENTRAL_HVAC:
            if self.cool_setpoint_range:
                allowed.add(Mode.COOL)
            if (
                self.auto_heat_range
                and self.auto_cool_range
                and self.auto_enabled is not False
            ):
                allowed.add(Mode.AUTO)
            allowed.add(Mode.FAN_ONLY)
        return [mode for mode in self.modes if mode in allowed]

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
        auto = _dict(climate.get("auto"))
        misc = _dict(_dict(climate.get("advancedConfig")).get("misc"))
        auto_enabled = _dict(misc.get("enableAutoMode")).get("value")
        return cls(
            raw=dict(data),
            auto_heat_range=value_range(valid(auto.get("heatSetpoint"))),
            auto_cool_range=value_range(valid(auto.get("coolSetpoint"))),
            min_setpoint_delta=_float(
                _dict(_dict(auto.get("constraints")).get("minSetpointDelta")).get(
                    "value"
                )
            ),
            auto_enabled=bool(auto_enabled)
            if isinstance(auto_enabled, bool)
            or (type(auto_enabled) is int and auto_enabled in (0, 1))
            else None,
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
    setpoint_min: float | None
    setpoint_max: float | None
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
            setpoint_min=_float(data.get("MinSetpoint")),
            setpoint_max=_float(data.get("MaxSetpoint")),
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

    # A Mini-Split AC's record describes its remote: SupportedModes maps each
    # mode (AC_MODE_KEYS) to the fan speeds and swing settings it can send, and
    # modifiedKeys, when present, lists the IR keys the user kept after
    # customizing the remote in Mysa's app.

    @property
    def modified_keys(self) -> frozenset[int] | None:
        """The remote's customized key list, or None if it wasn't customized."""
        keys = _ints(self.raw.get("modifiedKeys"))
        return frozenset(keys) if keys else None

    def _ac_mode(self, mode: Mode) -> dict[str, Any]:
        key = AC_MODE_KEYS.get(mode)
        if key is None:
            return {}
        return _dict(_dict(self.raw.get("SupportedModes")).get(str(key)))

    def ac_fan_speeds(self, mode: Mode) -> tuple[AcFan, ...]:
        """Fan speeds the AC's remote offers in a mode, slowest first."""
        keys = self.modified_keys
        speeds = {
            AcFan(v) for v in _ints(self._ac_mode(mode).get("fanSpeeds")) if v in AcFan
        }
        if keys is not None:
            speeds = {s for s in speeds if AC_FAN_KEYS[s] in keys}
        # AC_FAN_TO_STATE numbers them auto, low, medium, high, 1-7, sleep.
        return tuple(sorted(speeds, key=AC_FAN_TO_STATE.__getitem__))

    def ac_swing(self, mode: Mode, direction: str) -> tuple[Swing, ...]:
        """Swing settings the AC's remote offers in a mode (vertical or horizontal)."""
        keys = self.modified_keys
        if keys is not None and not keys & AC_SWING_KEYS[direction]:
            return ()
        values = _ints(self._ac_mode(mode).get(direction))
        return tuple(sorted({Swing(v) for v in values if v in Swing}))


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
    # Home ID -> name ("DefaultHome" unless the user renamed it).
    homes: dict[str, str] = field(default_factory=dict)

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
        home_ids = (
            tuple(h for h in homes if isinstance(h, str))
            if isinstance(homes, list)
            else ()
        )
        names: dict[str, str] = {}
        raw_homes = body.get("homes")
        for home in raw_homes if isinstance(raw_homes, list) else []:
            home_id = _str(_dict(home).get("Id"))
            if home_id:
                names[home_id] = _str(_dict(home).get("Name")) or home_id
        for home_id in home_ids:
            names.setdefault(home_id, home_id)
        return cls(
            user_id=_str(user.get("Id")),
            home_ids=home_ids,
            devices=devices,
            homes=names,
        )
