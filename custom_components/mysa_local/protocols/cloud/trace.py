"""Fixed route vocabulary for optional, transport-independent request tracing."""

import re

_ROUTES = (
    "/telemetry/usage/{id}",
    "/mysa-legacy/stream/home/{id}",
    "/mobile/bootstrap",
    "/devices",
    "/devices/state",
    "/devices/purgeadd",
    "/state/batch",
    "/state/{id}/update",
    "/state/{id}",
    "/state/history/{id}",
    "/devices/update_available/{id}",
    "/devices/{id}/firmware-update",
    "/devices/{id}",
    "/schedule/hold",
    "/schedule/hold/{id}",
    "/schedule/{id}/attach/{id}",
    "/schedule",
    "/schedules",
    "/schedules/v2",
    "/schedules/v2/{id}",
    "/schedules/device/update/{id}",
    "/capabilities/{id}",
)


def route_template(path: str) -> str:
    """Replace every dynamic segment; never emit unknown paths or query strings."""
    path = path.split("?", 1)[0].rstrip("/")
    for template in _ROUTES:
        if re.fullmatch(re.escape(template).replace(r"\{id\}", "[^/]+"), path):
            return template
    return "unknown"
