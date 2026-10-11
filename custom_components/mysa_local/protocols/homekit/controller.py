"""aiohomekit controller and pairing lifecycle hidden behind semantic methods."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from ipaddress import ip_address
import logging
from typing import Any
import uuid

from aiohomekit import Controller
from aiohomekit.characteristic_cache import CharacteristicCacheMemory
from aiohomekit.controller import ip
from aiohomekit.exceptions import (
    AccessoryDisconnectedError,
    AccessoryNotFoundError,
    AuthenticationError,
    BusyError,
    EncryptionError,
    HomeKitException,
    MalformedPinError,
    MaxPeersError,
    MaxTriesError,
    UnavailableError,
)

from .device import HomeKitDevice
from .exceptions import (
    HomeKitAuthenticationError,
    HomeKitBusyError,
    HomeKitConnectionError,
    HomeKitError,
    HomeKitMalformedPinError,
    HomeKitMaxPeersError,
    HomeKitMaxTriesError,
    HomeKitNotFoundError,
    HomeKitUnavailableError,
)
from .models import AccessoryInfo, HomeKitDiscovery, HomeKitEndpoint

PairingSaved = Callable[[Mapping[str, Any]], Awaitable[None]]
_KNOWN_PROTOCOL_ERRORS = (HomeKitException, TimeoutError, OSError)
PAIRING_CLEANUP_TIMEOUT = 15
PAIRING_CLOSE_TIMEOUT = 5
_LOGGER = logging.getLogger(__name__)


def _translate_error(error: BaseException) -> HomeKitError:
    """Map aiohomekit exceptions to stable protocol-level errors."""
    if isinstance(error, AuthenticationError):
        return HomeKitAuthenticationError("HomeKit authentication was rejected")
    if isinstance(error, BusyError):
        return HomeKitBusyError("The HomeKit accessory is busy")
    if isinstance(error, MaxTriesError):
        return HomeKitMaxTriesError("The HomeKit accessory rejected too many attempts")
    if isinstance(error, UnavailableError):
        return HomeKitUnavailableError("The HomeKit accessory is already paired")
    if isinstance(error, MalformedPinError):
        return HomeKitMalformedPinError("The HomeKit setup code is malformed")
    if isinstance(error, MaxPeersError):
        return HomeKitMaxPeersError("The HomeKit accessory has no free pairing slots")
    if isinstance(error, AccessoryNotFoundError):
        return HomeKitNotFoundError("HomeKit accessory was not found")
    if isinstance(
        error,
        (AccessoryDisconnectedError, EncryptionError, TimeoutError, OSError),
    ):
        return HomeKitConnectionError("HomeKit connection failed")
    if isinstance(error, HomeKitException):
        return HomeKitError("HomeKit request failed")
    return HomeKitError("HomeKit operation failed")


def _discovery(raw: Any) -> HomeKitDiscovery:
    """Copy the fields callers need from an aiohomekit discovery."""
    description = raw.description
    address = getattr(description, "address", None)
    addresses = getattr(description, "addresses", None)
    port = getattr(description, "port", None)
    endpoint = None
    if isinstance(port, int) and 1 <= port <= 65535:
        valid_addresses: list[str] = []
        candidates = addresses or ((address,) if address is not None else ())
        for candidate in candidates:
            try:
                parsed = ip_address(candidate)
            except TypeError, ValueError:
                continue
            if not parsed.is_link_local and not parsed.is_unspecified:
                valid_addresses.append(str(parsed))
        try:
            parsed_host = ip_address(str(address)) if address is not None else None
        except TypeError, ValueError:
            parsed_host = None
        if (
            parsed_host is None
            or parsed_host.is_link_local
            or parsed_host.is_unspecified
        ):
            parsed_host = ip_address(valid_addresses[0]) if valid_addresses else None
        if parsed_host is not None:
            endpoint = HomeKitEndpoint(
                host=str(parsed_host),
                addresses=tuple(dict.fromkeys(valid_addresses)) or (str(parsed_host),),
                port=port,
            )

    async def start_pairing() -> PairingSession:
        return await PairingSession.async_start(raw, str(description.id).lower())

    return HomeKitDiscovery(
        id=str(description.id).lower(),
        name=str(description.name),
        model=getattr(description, "model", None),
        paired=bool(raw.paired),
        endpoint=endpoint,
        _pairing_starter=start_pairing,
    )


class PairingSession:
    """An opaque pair-setup session retained while HA shows the PIN form."""

    def __init__(self, finish_pairing: Callable[[str], Awaitable[Any]]) -> None:
        """Store the finisher without returning aiohomekit's callback to HA."""
        self.__finish_pairing = finish_pairing

    @classmethod
    async def async_start(cls, discovery: Any, pairing_id: str) -> PairingSession:
        """Start HomeKit pair setup for the discovered accessory."""
        try:
            async with asyncio.timeout(30):
                finish_pairing = await discovery.async_start_pairing(pairing_id)
        except _KNOWN_PROTOCOL_ERRORS as error:
            raise _translate_error(error) from error
        return cls(finish_pairing)

    async def async_complete(
        self,
        pin: str,
        *,
        on_pairing_saved: PairingSaved,
    ) -> tuple[dict[str, Any], AccessoryInfo]:
        """Complete setup, durably save keys, then read accessory information."""
        try:
            async with asyncio.timeout(60):
                pairing = await self.__finish_pairing(pin)
        except _KNOWN_PROTOCOL_ERRORS as error:
            raise _translate_error(error) from error

        data = dict(pairing.pairing_data)
        try:
            # The accessory has accepted our keys. Persist immediately and
            # shield storage from cancellation before the next HAP request.
            await _persist_pairing(
                on_pairing_saved,
                data,
                lambda: self.__rollback_pairing(pairing, data),
            )

            # A read can fail after pairing succeeds. Keep the saved credentials
            # so the setup flow can resume without asking for the PIN again.
            device = HomeKitDevice(pairing, data)
            info = await device.async_get_info()
            return data, info
        finally:
            try:
                async with asyncio.timeout(PAIRING_CLOSE_TIMEOUT):
                    await pairing.close()
            except Exception as error:  # noqa: BLE001 - cleanup cannot mask result
                _LOGGER.debug(
                    "Closing a HomeKit pairing session failed (%s)",
                    type(error).__name__,
                )

    @staticmethod
    async def __rollback_pairing(pairing: Any, data: Mapping[str, Any]) -> None:
        """Remove the newly added admin key if saving it failed."""
        pairing_id = data.get("iOSPairingId")
        if not isinstance(pairing_id, str):
            return
        try:
            async with asyncio.timeout(PAIRING_CLEANUP_TIMEOUT):
                await pairing.remove_pairing(pairing_id)
        except Exception as error:  # noqa: BLE001 - preserve original save error
            _LOGGER.warning(
                "Rolling back a HomeKit pairing failed (%s)", type(error).__name__
            )


class HomeKitProtocol:
    """An aiohomekit controller exposed through semantic protocol operations."""

    def __init__(
        self,
        async_zeroconf_instance: Any | None = None,
        *,
        controller: Any | None = None,
    ) -> None:
        """Create the controller using an externally managed Zeroconf instance."""
        if controller is None:
            if async_zeroconf_instance is None:
                raise ValueError("HomeKit needs an async Zeroconf instance")
            controller = Controller(
                async_zeroconf_instance=async_zeroconf_instance,
                char_cache=CharacteristicCacheMemory(),
            )
        self.__controller = controller
        self.__started = async_zeroconf_instance is None

    @classmethod
    def from_controller(cls, controller: Any) -> HomeKitProtocol:
        """Wrap an existing aiohomekit controller behind the semantic API.

        This is useful for embedding and for protocol tests with a fake
        controller. Production HA code supplies its async Zeroconf instance.
        """
        return cls(controller=controller)

    async def async_start(self) -> None:
        """Start mDNS discovery and the HomeKit controller."""
        try:
            await self.__controller.async_start()
        except _KNOWN_PROTOCOL_ERRORS as error:
            raise _translate_error(error) from error
        self.__started = True

    async def async_stop(self) -> None:
        """Stop the controller and its discovery listeners."""
        if not self.__started:
            return
        try:
            await self.__controller.async_stop()
        except _KNOWN_PROTOCOL_ERRORS as error:
            raise _translate_error(error) from error
        finally:
            self.__started = False

    async def async_discover(self) -> AsyncIterator[HomeKitDiscovery]:
        """Yield discovered accessories as safe name/model/paired records."""
        try:
            async for raw in self.__controller.async_discover():
                yield _discovery(raw)
        except _KNOWN_PROTOCOL_ERRORS as error:
            raise _translate_error(error) from error

    async def async_find(self, pairing_id: str) -> HomeKitDiscovery:
        """Find an accessory by its HomeKit pairing identifier."""
        try:
            async with asyncio.timeout(30):
                raw = await self.__controller.async_find(pairing_id)
        except _KNOWN_PROTOCOL_ERRORS as error:
            raise _translate_error(error) from error
        return _discovery(raw)

    async def async_start_pairing(self, pairing_id: str) -> PairingSession:
        """Find an accessory and begin its pair-setup exchange."""
        discovery = await self.async_find(pairing_id)
        return await discovery.async_start_pairing()

    def load_pairing(
        self,
        pairing_data: Mapping[str, Any],
        *,
        on_endpoints_changed: Callable[[HomeKitEndpoint], None] | None = None,
    ) -> HomeKitDevice:
        """Load saved credentials as a semantic device session."""
        copied_data = dict(pairing_data)
        pairing_id = copied_data.get("AccessoryPairingID")
        if not isinstance(pairing_id, str) or not pairing_id:
            raise HomeKitError("HomeKit pairing data has no accessory identifier")
        alias = pairing_id
        try:
            pairing = self.__controller.load_pairing(alias, copied_data)
        except _KNOWN_PROTOCOL_ERRORS as error:
            raise _translate_error(error) from error
        device = HomeKitDevice(pairing, copied_data)
        if on_endpoints_changed is not None:
            device.add_endpoint_listener(on_endpoints_changed)
        return device

    async def async_unpair(self, pairing_data: Mapping[str, Any]) -> None:
        """Remove this controller's HomeKit pairing from an accessory."""
        copied_data = dict(pairing_data)
        pairing_id = copied_data.get("AccessoryPairingID")
        if not isinstance(pairing_id, str) or not pairing_id:
            raise HomeKitError("HomeKit pairing data has no accessory identifier")
        try:
            # Reuse the stable accessory ID used by load_pairing. Besides
            # avoiding duplicate live sessions, this works with controllers
            # that already index their saved pairing by that alias.
            self.__controller.load_pairing(pairing_id, copied_data)
            async with asyncio.timeout(15):
                await self.__controller.remove_pairing(pairing_id)
        except _KNOWN_PROTOCOL_ERRORS as error:
            raise _translate_error(error) from error

    async def async_copy_pairing(
        self,
        source: Mapping[str, Any],
        *,
        on_pairing_saved: PairingSaved,
    ) -> tuple[dict[str, Any], AccessoryInfo]:
        """Install and persist our own admin key using an existing pairing."""
        try:
            source_pairing = self.__controller.load_pairing(
                f"mysa-copy-source-{uuid.uuid4().hex}", dict(source)
            )
        except _KNOWN_PROTOCOL_ERRORS as error:
            raise _translate_error(error) from error
        if not isinstance(source_pairing, ip.IpPairing):
            raise HomeKitError("Only HomeKit over IP is supported")

        try:
            pairing_id, private_key, public_key = _generate_controller_keys()

            async def rollback() -> None:
                try:
                    async with asyncio.timeout(PAIRING_CLEANUP_TIMEOUT):
                        await source_pairing.remove_pairing(pairing_id)
                except Exception as error:  # noqa: BLE001 - preserve the original failure
                    _LOGGER.warning(
                        "Rolling back a copied HomeKit pairing failed (%s)",
                        type(error).__name__,
                    )

            try:
                async with asyncio.timeout(30):
                    await source_pairing.add_pairing(pairing_id, public_key, "Admin")
            except _KNOWN_PROTOCOL_ERRORS as error:
                # A lost response is ambiguous: the accessory may have accepted
                # the key before the transport failed. Removal is harmless when
                # it did not, and prevents leaving an orphaned controller key.
                await rollback()
                raise _translate_error(error) from error
            except asyncio.CancelledError:
                # Cancellation also obscures whether the accessory accepted the
                # request. Give remote cleanup its bounded chance, then keep the
                # caller's cancellation semantics.
                await rollback()
                raise

            data = dict(source)
            data.update(
                {
                    "iOSPairingId": pairing_id,
                    "iOSDeviceLTSK": private_key,
                    "iOSDeviceLTPK": public_key,
                }
            )

            await _persist_pairing(on_pairing_saved, data, rollback)

            new_device = self.load_pairing(data)
            try:
                info = await new_device.async_get_info()
            finally:
                try:
                    async with asyncio.timeout(PAIRING_CLOSE_TIMEOUT):
                        await new_device.async_close()
                except Exception as error:  # noqa: BLE001 - preserve info result
                    _LOGGER.debug(
                        "Closing a copied HomeKit pairing failed (%s)",
                        type(error).__name__,
                    )
            return data, info
        finally:
            try:
                async with asyncio.timeout(PAIRING_CLOSE_TIMEOUT):
                    await source_pairing.close()
            except Exception as error:  # noqa: BLE001 - cleanup cannot mask result
                _LOGGER.debug(
                    "Closing a source HomeKit pairing failed (%s)",
                    type(error).__name__,
                )


def _generate_controller_keys() -> tuple[str, str, str]:
    """Return a new pairing ID and Ed25519 controller keys as hexadecimal."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ed25519

    key = ed25519.Ed25519PrivateKey.generate()
    private_key = key.private_bytes(
        serialization.Encoding.Raw,
        serialization.PrivateFormat.Raw,
        serialization.NoEncryption(),
    ).hex()
    public_key = (
        key.public_key()
        .public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )
        .hex()
    )
    return str(uuid.uuid4()), private_key, public_key


async def _persist_pairing(
    callback: PairingSaved,
    data: Mapping[str, Any],
    rollback: Callable[[], Awaitable[None]],
) -> None:
    """Await credential storage shielded, rolling back only if it fails."""
    save_task = asyncio.ensure_future(callback(dict(data)))
    try:
        await asyncio.shield(save_task)
    except asyncio.CancelledError:
        try:
            await save_task
        except asyncio.CancelledError:
            await rollback()
        except Exception:  # noqa: BLE001 - storage failures require remote cleanup
            await rollback()
        raise
    except Exception:
        await rollback()
        raise
