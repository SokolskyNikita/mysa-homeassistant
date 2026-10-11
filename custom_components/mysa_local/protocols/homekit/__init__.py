"""Semantic HomeKit protocol boundary used by Mysa's HA adapters."""

from .advertisement import (
    pairing_identity,
    parse_advertisement,
    update_pairing_endpoint,
)
from .controller import HomeKitProtocol, PairingSession
from .device import HomeKitDevice
from .exceptions import (
    HomeKitAuthenticationError,
    HomeKitBusyError,
    HomeKitCharacteristicReadError,
    HomeKitConnectionError,
    HomeKitError,
    HomeKitMalformedPinError,
    HomeKitMaxPeersError,
    HomeKitMaxTriesError,
    HomeKitNotFoundError,
    HomeKitUnavailableError,
    HomeKitWriteRejectedError,
)
from .models import (
    AccessoryInfo,
    CharacteristicInfo,
    Field,
    HomeKitAdvertisement,
    HomeKitDiscovery,
    HomeKitEndpoint,
    HomeKitEvent,
    LocalAction,
    LocalMode,
)

__all__ = [
    "AccessoryInfo",
    "CharacteristicInfo",
    "Field",
    "HomeKitAdvertisement",
    "HomeKitAuthenticationError",
    "HomeKitBusyError",
    "HomeKitCharacteristicReadError",
    "HomeKitConnectionError",
    "HomeKitDevice",
    "HomeKitDiscovery",
    "HomeKitEndpoint",
    "HomeKitError",
    "HomeKitEvent",
    "HomeKitMalformedPinError",
    "HomeKitMaxPeersError",
    "HomeKitMaxTriesError",
    "HomeKitNotFoundError",
    "HomeKitProtocol",
    "HomeKitUnavailableError",
    "HomeKitWriteRejectedError",
    "LocalAction",
    "LocalMode",
    "PairingSession",
    "pairing_identity",
    "parse_advertisement",
    "update_pairing_endpoint",
]
