"""Runtime data kept on the config entry."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from homeassistant.config_entries import ConfigEntry

from .event_history import EventHistory

if TYPE_CHECKING:
    from mysa_client.cloud import MysaClient

    from .cloud_coordinator import MysaCloudCoordinator
    from .mqtt_coordinator import MysaMqttCoordinator
    from .thermostat import Thermostat


@dataclass
class MysaRuntimeData:
    """What a loaded Mysa Local entry holds."""

    client: MysaClient | None
    cloud: MysaCloudCoordinator | None
    thermostats: dict[str, Thermostat] = field(default_factory=dict)
    fingerprint: object = None
    history: EventHistory = field(default_factory=EventHistory)
    mqtt: MysaMqttCoordinator | None = None

    @property
    def cloud_enabled(self) -> bool:
        """True when cloud calls are allowed."""
        return self.cloud is not None


type MysaConfigEntry = ConfigEntry[MysaRuntimeData]
