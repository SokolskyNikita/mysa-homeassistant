"""Pairing protocol behavior when the accessory's reply is uncertain."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from aiohomekit.controller import ip
from aiohomekit.exceptions import AccessoryDisconnectedError
import pytest

from custom_components.mysa_local.protocols.homekit import (
    HomeKitConnectionError,
    HomeKitProtocol,
)

from .common import PAIRING_DATA


def _protocol_with_source_pairing(pairing: ip.IpPairing) -> HomeKitProtocol:
    """Build a protocol around a fake controller that returns one pairing."""
    controller = SimpleNamespace(load_pairing=Mock(return_value=pairing))
    return HomeKitProtocol.from_controller(controller)


def _source_pairing() -> ip.IpPairing:
    """Create an isolated mock that satisfies the protocol's IP check."""
    pairing = Mock(spec=ip.IpPairing)
    pairing.add_pairing = AsyncMock()
    pairing.remove_pairing = AsyncMock()
    pairing.close = AsyncMock()
    return pairing


async def test_lost_add_pairing_reply_rolls_back_accepted_key() -> None:
    """An accepted key is removed if add_pairing loses its response."""
    source_pairing = _source_pairing()
    remote_keys: set[str] = set()

    async def add_then_lose_reply(
        pairing_id: str, _public_key: str, _permission: str
    ) -> None:
        remote_keys.add(pairing_id)
        raise AccessoryDisconnectedError("response lost after accessory accepted key")

    async def remove(pairing_id: str) -> None:
        remote_keys.discard(pairing_id)

    source_pairing.add_pairing.side_effect = add_then_lose_reply
    source_pairing.remove_pairing.side_effect = remove
    protocol = _protocol_with_source_pairing(source_pairing)
    save_pairing = AsyncMock()

    with (
        patch(
            "custom_components.mysa_local.protocols.homekit.controller._generate_controller_keys",
            return_value=("new-controller-id", "private-key", "public-key"),
        ),
        pytest.raises(HomeKitConnectionError),
    ):
        await protocol.async_copy_pairing(PAIRING_DATA, on_pairing_saved=save_pairing)

    source_pairing.add_pairing.assert_awaited_once_with(
        "new-controller-id", "public-key", "Admin"
    )
    source_pairing.remove_pairing.assert_awaited_once_with("new-controller-id")
    source_pairing.close.assert_awaited_once_with()
    save_pairing.assert_not_awaited()
    assert remote_keys == set()


async def test_unpairable_rollback_does_not_hide_lost_add_reply() -> None:
    """A stalled best-effort rollback must stay bounded and retain the cause."""
    source_pairing = _source_pairing()
    remote_keys: set[str] = set()
    rollback_started = asyncio.Event()
    never_release = asyncio.Event()

    async def add_then_lose_reply(
        pairing_id: str, _public_key: str, _permission: str
    ) -> None:
        remote_keys.add(pairing_id)
        raise AccessoryDisconnectedError("response lost after accessory accepted key")

    async def stalled_remove(pairing_id: str) -> None:
        rollback_started.set()
        await never_release.wait()
        remote_keys.discard(pairing_id)

    source_pairing.add_pairing.side_effect = add_then_lose_reply
    source_pairing.remove_pairing.side_effect = stalled_remove
    protocol = _protocol_with_source_pairing(source_pairing)
    save_pairing = AsyncMock()

    with (
        patch(
            "custom_components.mysa_local.protocols.homekit.controller._generate_controller_keys",
            return_value=("new-controller-id", "private-key", "public-key"),
        ),
        patch(
            "custom_components.mysa_local.protocols.homekit.controller.PAIRING_CLEANUP_TIMEOUT",
            0.01,
        ),
        pytest.raises(HomeKitConnectionError),
    ):
        await asyncio.wait_for(
            protocol.async_copy_pairing(PAIRING_DATA, on_pairing_saved=save_pairing),
            timeout=0.5,
        )

    assert rollback_started.is_set()
    source_pairing.close.assert_awaited_once_with()
    save_pairing.assert_not_awaited()


async def test_cancelled_add_pairing_rolls_back_an_accepted_key() -> None:
    """Cancellation during an add with no reply also has an ambiguous outcome."""
    source_pairing = _source_pairing()
    remote_keys: set[str] = set()
    accepted = asyncio.Event()
    never_release = asyncio.Event()

    async def add_then_wait(
        pairing_id: str, _public_key: str, _permission: str
    ) -> None:
        remote_keys.add(pairing_id)
        accepted.set()
        await never_release.wait()

    async def remove(pairing_id: str) -> None:
        remote_keys.discard(pairing_id)

    source_pairing.add_pairing.side_effect = add_then_wait
    source_pairing.remove_pairing.side_effect = remove
    protocol = _protocol_with_source_pairing(source_pairing)
    save_pairing = AsyncMock()

    with patch(
        "custom_components.mysa_local.protocols.homekit.controller._generate_controller_keys",
        return_value=("new-controller-id", "private-key", "public-key"),
    ):
        task = asyncio.create_task(
            protocol.async_copy_pairing(PAIRING_DATA, on_pairing_saved=save_pairing)
        )
        await accepted.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    source_pairing.add_pairing.assert_awaited_once_with(
        "new-controller-id", "public-key", "Admin"
    )
    source_pairing.remove_pairing.assert_awaited_once_with("new-controller-id")
    source_pairing.close.assert_awaited_once_with()
    save_pairing.assert_not_awaited()
    assert remote_keys == set()
