"""System health: how the thermostats are connected and how the cloud is doing."""

from __future__ import annotations

from collections import Counter
from typing import Any

from homeassistant.components import system_health
from homeassistant.const import CONF_PASSWORD
from homeassistant.core import HomeAssistant, callback

from .const import (
    CONF_REFRESH_TOKEN,
    CONNECTION_CLOUD,
    CONNECTION_LOCAL,
    CONNECTION_OFFLINE,
    DOMAIN,
    VERSION,
)
from .data import MysaConfigEntry
from .protocols.cloud.const import API_BASE


@callback
def async_register(
    hass: HomeAssistant, register: system_health.SystemHealthRegistration
) -> None:
    """Register the info callback."""
    register.async_register_info(system_health_info)


async def system_health_info(hass: HomeAssistant) -> dict[str, Any]:
    """Thermostats per connection path, sign-in state and cloud reachability."""
    entries: list[MysaConfigEntry] = hass.config_entries.async_loaded_entries(DOMAIN)
    info: dict[str, Any] = {"version": VERSION}
    if not entries:
        return info
    entry = entries[0]
    runtime = entry.runtime_data
    paths = Counter(t.connection_path for t in runtime.thermostats.values())
    info.update(
        {
            "thermostats": len(runtime.thermostats),
            "local": paths[CONNECTION_LOCAL],
            "cloud": paths[CONNECTION_CLOUD],
            "offline": paths[CONNECTION_OFFLINE],
            "signed_in": bool(entry.data.get(CONF_REFRESH_TOKEN)),
            "password_saved": CONF_PASSWORD in entry.data,
            "cloud_enabled": runtime.cloud_enabled,
        }
    )
    cloud = runtime.cloud
    if cloud is not None:
        # Only with the cloud on: with it off, nothing may contact Mysa.
        info["cloud_last_update"] = "ok" if cloud.last_update_success else "failed"
        if cloud.update_interval is not None:
            info["cloud_poll_interval"] = (
                f"{int(cloud.update_interval.total_seconds())} s"
            )
        info["can_reach_server"] = system_health.async_check_can_reach_url(
            hass, API_BASE
        )
    return info
