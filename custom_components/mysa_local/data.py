"""Runtime data kept on the config entry."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from homeassistant.config_entries import ConfigEntry

if TYPE_CHECKING:
    from .api import MysaClient
    from .cloud import MysaCloudCoordinator
    from .thermostat import Thermostat


@dataclass
class MysaRuntimeData:
    """What a loaded Mysa Local entry holds."""

    client: MysaClient | None
    cloud: MysaCloudCoordinator | None
    thermostats: dict[str, Thermostat] = field(default_factory=dict)
    fingerprint: object = None

    @property
    def cloud_enabled(self) -> bool:
        """True when cloud calls are allowed."""
        return self.cloud is not None


type MysaConfigEntry = ConfigEntry[MysaRuntimeData]
