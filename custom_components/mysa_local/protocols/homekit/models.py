"""Semantic types shared by the HomeKit protocol and HA adapter."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .controller import PairingSession


class Field(StrEnum):
    """A Mysa value exposed by its HomeKit thermostat service."""

    CURRENT_TEMPERATURE = "current_temperature"
    TARGET_TEMPERATURE = "target_temperature"
    CURRENT_ACTION = "current_action"
    TARGET_MODE = "target_mode"
    DISPLAY_UNIT = "display_unit"


class LocalMode(StrEnum):
    """Modes supported by the HomeKit thermostat service."""

    OFF = "off"
    HEAT = "heat"
    COOL = "cool"
    AUTO = "auto"


class LocalAction(StrEnum):
    """Observed thermostat action, translated from HAP current-state values."""

    OFF = "off"
    IDLE = "idle"
    HEATING = "heating"
    COOLING = "cooling"


@dataclass(frozen=True, slots=True)
class CharacteristicInfo:
    """Limits advertised for a semantic HomeKit field."""

    min_value: float | None = None
    max_value: float | None = None
    min_step: float | None = None


@dataclass(frozen=True, slots=True)
class HomeKitEndpoint:
    """Network address reported by HomeKit discovery or a paired session."""

    host: str
    addresses: tuple[str, ...]
    port: int


@dataclass(frozen=True, slots=True)
class HomeKitAdvertisement:
    """Mysa identity and endpoint parsed from a HomeKit mDNS advertisement."""

    id: str
    name: str
    model: str
    paired: bool
    endpoint: HomeKitEndpoint


@dataclass(frozen=True, slots=True)
class AccessoryInfo:
    """Useful Mysa identity values read from its HomeKit accessory database."""

    name: str | None = None
    model: str | None = None
    manufacturer: str | None = None
    serial: str | None = None
    firmware: str | None = None
    mac: str | None = None

    @property
    def device_id(self) -> str | None:
        """Return Mysa's cloud ID (the Wi-Fi MAC without separators)."""
        if not self.mac:
            return None
        normalized = "".join(
            char for char in self.mac.lower() if char in "0123456789abcdef"
        )
        return normalized if len(normalized) == 12 else None


@dataclass(frozen=True, slots=True)
class HomeKitEvent:
    """A protocol event after characteristic IDs and HAP statuses are removed."""

    values: Mapping[Field, Any]
    failed_fields: Mapping[Field, str]
    reconnected: bool = False

    @classmethod
    def create(
        cls,
        values: Mapping[Field, Any] | None = None,
        failed_fields: Mapping[Field, str] | None = None,
        *,
        reconnected: bool = False,
    ) -> HomeKitEvent:
        """Copy event payloads into immutable mappings."""
        return cls(
            MappingProxyType(dict(values or {})),
            MappingProxyType(dict(failed_fields or {})),
            reconnected,
        )


@dataclass(frozen=True, slots=True)
class HomeKitDiscovery:
    """The safe subset of a HomeKit network discovery used by the integration."""

    id: str
    name: str
    model: str | None
    paired: bool
    endpoint: HomeKitEndpoint | None = None
    _pairing_starter: Callable[[], Awaitable[PairingSession]] | None = None

    async def async_start_pairing(self) -> PairingSession:
        """Start pair setup and return an opaque, resumable session."""
        if self._pairing_starter is None:
            raise RuntimeError("This HomeKit discovery cannot start pairing")
        return await self._pairing_starter()


def readonly(mapping: Mapping[Any, Any]) -> Mapping[Any, Any]:
    """Return a defensive immutable copy of a protocol mapping."""
    return MappingProxyType(dict(mapping))
