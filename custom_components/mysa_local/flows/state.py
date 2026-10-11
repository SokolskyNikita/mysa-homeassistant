"""State owned by one flow while pairing or linking a thermostat."""

from dataclasses import dataclass, field
from typing import Any

from custom_components.mysa_local.protocols.homekit import (
    HomeKitDiscovery,
    PairingSession,
)


@dataclass(slots=True)
class ThermostatFlowState:
    """Shared step state, initialized separately for setup, subentries, and repairs.

    Flow-specific pending entries and setup queues stay on their owning flow.
    None means that a selection or pairing session has not been started; empty
    discovery and recovery maps are valid before the first network scan.
    """

    hkid: str | None = None
    name: str | None = None
    model: str | None = None
    finish_pairing: PairingSession | None = field(default=None, repr=False)
    pairing_in_progress: bool = False
    discoveries: dict[str, HomeKitDiscovery] = field(default_factory=dict)
    pending_recoveries: dict[str, dict[str, Any]] = field(
        default_factory=dict, repr=False
    )
    takeover_entry_id: str | None = None

    target_device_id: str | None = None
    target_name: str | None = None
    account_device: tuple[str, str | None, str] | None = None
    account_optional: bool = False
    homes: dict[str, str] | None = None
