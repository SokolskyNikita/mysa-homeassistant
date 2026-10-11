"""Holds and weekly schedules (cloud features)."""

from __future__ import annotations

from datetime import datetime, time
from functools import partial
from typing import TYPE_CHECKING, Any

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .api import Mode, MysaNotFoundError
from .api.const import SETPOINT_SECTION_BY_MODE
from .cloud import translated_error
from .const import VERSION

if TYPE_CHECKING:
    from .thermostat import Thermostat

# Home Assistant weekday names -> Mysa dayOfWeek (0 = Sunday).
DAY_NUMBERS = {"sun": 0, "mon": 1, "tue": 2, "wed": 3, "thu": 4, "fri": 5, "sat": 6}
CLIENT_KEY = f"HomeAssistant-{VERSION}"


def _state_body(mode: Mode, temperature: float | None) -> dict[str, Any]:
    body: dict[str, Any] = {"modes": {"mode": int(mode)}}
    section = SETPOINT_SECTION_BY_MODE.get(mode)
    if section and temperature is not None:
        body[section] = {"setpoint": temperature}
    return body


def _home_id(thermostat: Thermostat) -> str:
    record = thermostat.cloud_record
    if record is None or not record.home_id:
        raise translated_error("cloud_required", name=thermostat.name)
    return record.home_id


async def async_hold(
    hass: HomeAssistant,
    thermostat: Thermostat,
    temperature: float | None,
    mode: Mode | None,
    until: datetime | None,
) -> None:
    """Hold a state over the schedule, permanently or until a time."""
    cloud = thermostat.require_cloud()
    mode = mode or thermostat.mode or Mode.HEAT
    if temperature is None:
        temperature = thermostat.target_temperature
    if until is not None:
        until = dt_util.as_utc(
            until
            if until.tzinfo
            else until.replace(tzinfo=dt_util.get_default_time_zone())
        )
        if until <= dt_util.utcnow():
            raise translated_error("hold_in_past")
    device_id = thermostat.device_id
    state = _state_body(mode, temperature)

    async def _hold() -> None:
        try:
            await cloud.client.set_hold(device_id, state, until=until)
        except MysaNotFoundError as err:
            # "Device ... is not following any schedule. Cannot create a hold."
            raise translated_error("hold_needs_schedule", name=thermostat.name) from err

    await cloud.async_call(_hold, stale=[f"hold:{device_id}"])
    # Mysa applies the held state from its own scheduler once the hold starts;
    # apply it now as well (local first), so the thermostat changes right away.
    if thermostat.mode != mode:
        await thermostat.async_set_mode(mode)
    if temperature is not None and thermostat.target_temperature != temperature:
        await thermostat.async_set_temperature(temperature)


async def async_resume_schedule(thermostat: Thermostat) -> None:
    """End a hold so the thermostat follows its schedule again."""
    cloud = thermostat.require_cloud()
    device_id = thermostat.device_id
    await cloud.async_call(
        lambda: cloud.client.resume_schedule(device_id), stale=[f"hold:{device_id}"]
    )


async def _async_schedules_for(thermostat: Thermostat) -> list[dict[str, Any]]:
    cloud = thermostat.require_cloud()
    schedules = await cloud.client.get_schedules(_home_id(thermostat))
    return [s for s in schedules if thermostat.device_id in (s.get("deviceIds") or [])]


def build_events(
    events: list[dict[str, Any]], default_mode: Mode
) -> list[dict[str, Any]]:
    """Turn the action's events into Mysa schedule events."""
    result: list[dict[str, Any]] = []
    for event in events:
        at: time = event["time"]
        mode = Mode[str(event.get("hvac_mode", default_mode.name)).upper()]
        state = _state_body(mode, event.get("temperature"))
        result.extend(
            {
                "executionTime": at.strftime("%H:%M:00"),
                "dayOfWeek": str(DAY_NUMBERS[day]),
                "clientKey": CLIENT_KEY,
                "state": state,
            }
            for day in event["days"]
        )
    result.sort(key=lambda e: (int(e["dayOfWeek"]), e["executionTime"]))
    return result


async def async_set_schedule(
    thermostat: Thermostat, events: list[dict[str, Any]]
) -> None:
    """Replace the thermostat's weekly schedule."""
    cloud = thermostat.require_cloud()
    device_id = thermostat.device_id
    model = thermostat.model or ""
    mysa_events = build_events(events, Mode.HEAT)
    existing = await _async_schedules_for(thermostat)
    for schedule in existing:
        schedule_id = str(schedule.get("id"))
        await cloud.async_call(
            partial(cloud.client.delete_schedule, schedule_id, device_id)
        )
    await cloud.async_call(
        lambda: cloud.client.create_schedule(
            home_id=_home_id(thermostat),
            device_id=device_id,
            device_type=model.split("-", 1)[0] or "BB",
            events=mysa_events,
        ),
        stale=[f"hold:{device_id}"],
    )


async def async_clear_schedule(thermostat: Thermostat) -> None:
    """Delete every schedule attached to the thermostat."""
    cloud = thermostat.require_cloud()
    device_id = thermostat.device_id
    for schedule in await _async_schedules_for(thermostat):
        schedule_id = str(schedule.get("id"))
        await cloud.async_call(
            partial(cloud.client.delete_schedule, schedule_id, device_id)
        )
