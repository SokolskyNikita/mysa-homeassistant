"""HomeKit protocol errors translated into integration-independent types."""

from __future__ import annotations

from collections.abc import Iterable

from .models import Field


class HomeKitError(Exception):
    """Base class for failures from a HomeKit thermostat."""


class HomeKitConnectionError(HomeKitError):
    """The accessory could not be reached or its encrypted session failed."""


class HomeKitNotFoundError(HomeKitConnectionError):
    """The accessory is no longer discoverable on the network."""


class HomeKitAuthenticationError(HomeKitError):
    """The accessory rejected the pairing credentials or setup code."""


class HomeKitUnavailableError(HomeKitError):
    """The accessory is already paired and will not start pair setup."""


class HomeKitBusyError(HomeKitError):
    """The accessory is handling another pairing operation."""


class HomeKitMaxTriesError(HomeKitError):
    """The accessory has temporarily blocked additional pairing attempts."""


class HomeKitMaxPeersError(HomeKitError):
    """The accessory has no free pairing slots."""


class HomeKitMalformedPinError(HomeKitError):
    """The supplied HomeKit setup code is malformed."""


class HomeKitWriteRejectedError(HomeKitError):
    """The accessory returned a failed status for a requested field."""


class HomeKitCharacteristicReadError(HomeKitError):
    """One or more required semantic values could not be read."""

    def __init__(self, message: str, fields: Iterable[Field] = ()) -> None:
        """Keep the failed fields available without exposing HAP identifiers."""
        super().__init__(message)
        self.fields = frozenset(fields)
