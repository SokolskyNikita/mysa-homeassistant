"""Per-field cloud reconciliation and pending-command confirmation policy."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import Any

from .protocols.cloud.const import EXPERIMENTAL_CLOUD_FAMILIES, model_family
from .protocols.cloud.models import DeviceRecord, DeviceState
from .protocols.cloud.observations import (
    MEASUREMENTS,
    STATE_FIELDS,
    TELEMETRY_FIELDS,
    Observation,
    observe,
    valid_value,
)

_LATEST_CONFIG = frozenset({"heat_setpoint", "mode", "button_lock"})
_PRIORITY = {"S": 3, "R": 2, "L": 1}


def reconcile(
    device_id: str,
    states: list[DeviceState],
    record: DeviceRecord | None,
    previous: DeviceState | None,
    now: datetime,
) -> DeviceState:
    """Select independent fields without lending them a sibling's timestamp."""
    if record and model_family(record.model) in EXPERIMENTAL_CLOUD_FAMILIES:
        record = None
    candidates: dict[str, list[Observation]] = {}
    for state in [*states, *([previous] if previous else [])]:
        for name, obs in (state.reported_observations or state.observations).items():
            if obs.usable(now):
                candidates.setdefault(name, []).append(obs)
    selected: dict[str, Observation] = {}
    for name, options in candidates.items():
        reported = [o for o in options if o.kind == "reported"]
        if not reported:
            continue
        timed = [o for o in reported if o.observed_at is not None]
        if (
            name in MEASUREMENTS
            or name == "connected"
            or (name in _LATEST_CONFIG and len(timed) == len(reported))
        ):
            if timed:
                selected[name] = max(
                    timed, key=lambda o: (o.observed_at, _PRIORITY.get(o.source, 0))
                )
        else:
            selected[name] = max(
                reported,
                key=lambda o: (
                    _PRIORITY.get(o.source, 0),
                    o.observed_at or datetime.min.replace(tzinfo=now.tzinfo),
                    o.fetched_at,
                ),
            )
    reported_selected = dict(selected)
    if record:
        stored = {
            "display_unit": record.display_unit,
            "control_type": record.duty_cycle_opt,
            "setpoint_min": record.setpoint_min,
            "setpoint_max": record.setpoint_max,
            "active_brightness": record.raw.get("MaxBrightness"),
            "idle_brightness": record.raw.get("MinBrightness"),
            "button_lock": {"Locked": 3, "Unlocked": 0}.get(
                str(record.raw.get("ButtonState"))
            ),
        }
        for name, value in stored.items():
            if valid_value(name, value) and (
                name == "display_unit" or name not in selected
            ):
                # A cached record is configuration, never a fresh device observation.
                old = previous.observations.get(name) if previous else None
                selected[name] = (
                    old
                    if old and old.kind == "stored" and old.value == value
                    else observe(value, "REC", None, now, kind="stored")
                )
    # Select a coherent pair from a single source. A lone S bound must not
    # be mixed with a record bound from an unrelated revision.
    for low, high in [
        ("setpoint_min", "setpoint_max"),
        ("idle_brightness", "active_brightness"),
    ]:
        pairs: list[tuple[Observation, Observation]] = []
        for state in [*states, *([previous] if previous else [])]:
            left = state.observations.get(low)
            right = state.observations.get(high)
            if (
                left
                and right
                and left.source == right.source
                and left.usable(now)
                and right.usable(now)
                and left.value <= right.value
            ):
                pairs.append((left, right))
        if record:
            left_value = (
                record.setpoint_min
                if low == "setpoint_min"
                else record.raw.get("MinBrightness")
            )
            right_value = (
                record.setpoint_max
                if high == "setpoint_max"
                else record.raw.get("MaxBrightness")
            )
            if (
                left_value is not None
                and right_value is not None
                and valid_value(low, left_value)
                and valid_value(high, right_value)
                and left_value <= right_value
            ):
                pairs.append(
                    (
                        observe(left_value, "REC", None, now, kind="stored"),
                        observe(right_value, "REC", None, now, kind="stored"),
                    )
                )
        if pairs:
            left, right = max(
                pairs,
                key=lambda pair: (
                    _PRIORITY.get(pair[0].source, 0),
                    pair[0].observed_at or datetime.min.replace(tzinfo=now.tzinfo),
                    pair[0].fetched_at,
                ),
            )
            selected[low], selected[high] = left, right
        else:
            selected.pop(low, None)
            selected.pop(high, None)
    base = next(
        (s for s in states if any(o.source == "S" for o in s.observations.values())),
        previous,
    )
    base = base or DeviceState.from_api(device_id, {})
    values: dict[str, Any] = {
        name: selected[name].value if name in selected else None
        for name in (*STATE_FIELDS, *TELEMETRY_FIELDS, "connected")
    }
    room = selected.get("room_temperature")
    connection = selected.get("connected")
    return replace(
        base,
        **values,
        observations=selected,
        reported_observations=reported_selected,
        reading_time=room.observed_at if room else None,
        last_connected=connection.observed_at if connection else None,
    )


@dataclass(slots=True)
class PendingCommand:
    """An accepted/ambiguous write tracked independently of confirmed state."""

    expected: dict[str, Any]
    submitted_at: datetime
    expires_at: datetime
    baseline: dict[str, Observation]
    correlation_id: str = ""
    evidence: dict[str, Observation] = field(default_factory=dict)
    status: str = "pending"
    confirmation: dict[str, str] = field(default_factory=dict)
    stored_fields: dict[str, Any] = field(default_factory=dict)

    def reconcile(
        self, state: DeviceState | None, record: DeviceRecord | None, now: datetime
    ) -> None:
        """Require a post-submission device report; REC confirms storage only."""
        if self.status not in ("pending", "ambiguous", "stored"):
            return
        for name, expected in self.expected.items():
            obs = state.reported_observations.get(name) if state else None
            old = self.baseline.get(name)
            if (
                obs
                and obs.kind == "reported"
                and obs.value == expected
                and obs.fetched_at >= self.submitted_at
                and obs.observed_at is not None
                and obs.observed_at >= self.submitted_at.replace(microsecond=0)
                and (old is None or old.value != obs.value)
            ):
                self.confirmation[name] = obs.source
                self.evidence[name] = obs
        if self.expected and len(self.confirmation) == len(self.expected):
            self.status = "confirmed"
        elif now >= self.expires_at:
            self.status = "expired"
        elif (
            record
            and self.stored_fields
            and all(record.raw.get(k) == v for k, v in self.stored_fields.items())
        ):
            self.status = "stored"


def pending_command(
    expected: dict[str, Any],
    state: DeviceState | None,
    now: datetime,
    stored: dict[str, Any] | None = None,
) -> PendingCommand:
    """Take a pre-write snapshot without changing any observed value."""
    baseline = dict(state.reported_observations or state.observations) if state else {}
    command = PendingCommand(
        expected, now, now + timedelta(minutes=2), baseline, stored_fields=stored or {}
    )
    command.confirmation = {
        name: "unchanged"
        for name, value in expected.items()
        if name in baseline
        and baseline[name].kind == "reported"
        and baseline[name].value == value
    }
    if expected and all(
        name in baseline
        and baseline[name].kind == "reported"
        and baseline[name].value == value
        for name, value in expected.items()
    ):
        command.status = "unchanged"
    return command
