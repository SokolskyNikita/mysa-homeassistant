"""Native schedule snapshots used only for inspection, clearing and recovery."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from .exceptions import MysaApiError

WEEKDAYS = (
    "Sunday",
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
)


def native_schedule(
    origin: str, raw: dict[str, Any], device: str, model: str, home: str
) -> dict[str, Any] | None:
    """Preserve the native body and tag its origin for durable recovery."""
    if origin == "v1":
        events = raw.get("ScheduledActions")
        if (
            not isinstance(events, dict)
            or set(events) != set(WEEKDAYS)
            or any(not isinstance(v, list) for v in events.values())
        ):
            raise MysaApiError("Malformed legacy v1 schedule")
        if any(
            not isinstance(event, dict)
            or not isinstance(event.get("Minute"), int)
            or isinstance(event.get("Minute"), bool)
            or not 0 <= event["Minute"] < 1440
            for day in events.values()
            for event in day
        ):
            raise MysaApiError("Malformed legacy v1 event")
        identity = f"v1:{device}"
    else:
        events = raw.get("Events")
        if (
            not isinstance(events, dict)
            or not isinstance(raw.get("Id"), str)
            or "Version" not in raw
            or not isinstance(raw.get("Owner"), str)
        ):
            raise MysaApiError("Malformed legacy v2 schedule")
        if any(not isinstance(v, dict) for v in events.values()):
            raise MysaApiError("Malformed legacy v2 events")
        for day, minutes in events.items():
            if day not in tuple(str(n) for n in range(7)):
                raise MysaApiError("Malformed legacy schedule weekday")
            for minute, commands in minutes.items():
                if (
                    not isinstance(minute, str)
                    or not minute.isdigit()
                    or not 0 <= int(minute) < 1440
                    or not isinstance(commands, dict)
                    or not commands
                    or any(
                        not isinstance(command, str) for command in commands.values()
                    )
                ):
                    raise MysaApiError("Malformed legacy v2 event")
        identity = raw["Id"]
    if not any(events.values()):
        return None
    if not model:
        raise MysaApiError("Missing legacy schedule device model")
    return {
        "id": identity,
        "origin": origin,
        "native": deepcopy(raw),
        "name": identity,
        "homeId": home,
        "deviceType": model,
        "reusable": False,
        "deviceIds": [device],
        "events": [{"native": deepcopy(events)}],
    }
