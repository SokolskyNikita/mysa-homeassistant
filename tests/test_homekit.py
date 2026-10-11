"""Tests for the shared HomeKit controller helpers."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from unittest.mock import AsyncMock, patch

from homeassistant.core import HomeAssistant
import pytest

from custom_components.mysa_local import homekit

from .common import PAIRING_DATA, lite_accessories


class _FakeIpPairing:
    """Small fake for the pairing operations used by async_copy_pairing."""

    def __init__(self, name: str, events: list[str]) -> None:
        self.name = name
        self.events = events
        self.accessories = None

    async def add_pairing(self, *_args: Any) -> None:
        self.events.append("add_pairing")

    async def list_accessories_and_characteristics(self) -> None:
        self.events.append("list_accessories")
        self.accessories = lite_accessories()

    async def close(self) -> None:
        self.events.append(f"close_{self.name}")

    async def remove_pairing(self, _pairing_id: str) -> None:
        self.events.append("remove_pairing")


class _FakeController:
    """Return separate source and newly-created pairing objects."""

    def __init__(self, *, events: list[str], **_kwargs: Any) -> None:
        self.events = events
        self.old = _FakeIpPairing("old", events)
        self.new = _FakeIpPairing("new", events)

    async def async_start(self) -> None:
        self.events.append("start")

    async def async_stop(self) -> None:
        self.events.append("stop")

    def load_pairing(self, alias: str, _data: Mapping[str, Any]) -> _FakeIpPairing:
        self.events.append(f"load_{alias[:4]}")
        return self.old if alias.startswith("copy-src") else self.new


async def test_async_copy_pairing_persists_before_accessory_read(
    hass: HomeAssistant,
) -> None:
    """Save newly installed controller keys before a fallible network read."""
    events: list[str] = []
    controllers: list[_FakeController] = []

    def controller_factory(**kwargs: Any) -> _FakeController:
        controller = _FakeController(events=events, **kwargs)
        controllers.append(controller)
        return controller

    async def save_pairing(data: Mapping[str, Any]) -> None:
        assert data["AccessoryPairingID"] == PAIRING_DATA["AccessoryPairingID"]
        assert data["iOSPairingId"]
        events.append("persist")

    with (
        patch.object(homekit, "Controller", side_effect=controller_factory),
        patch.object(homekit.ip, "IpPairing", _FakeIpPairing),
        patch.object(
            homekit.zeroconf,
            "async_get_async_instance",
            AsyncMock(return_value=object()),
        ),
    ):
        await homekit.async_copy_pairing(
            hass, PAIRING_DATA, on_pairing_saved=save_pairing
        )

    assert events.index("add_pairing") < events.index("persist")
    assert events.index("persist") < events.index("list_accessories")
    assert events[-1] == "stop"
    assert len(controllers) == 1


async def test_async_copy_pairing_rolls_back_if_backup_fails(
    hass: HomeAssistant,
) -> None:
    """Undo the accessory-side pairing when its credentials cannot be saved."""
    events: list[str] = []

    async def fail_to_save(_data: Mapping[str, Any]) -> None:
        events.append("persist")
        raise OSError("storage unavailable")

    with (
        patch.object(
            homekit,
            "Controller",
            side_effect=lambda **kwargs: _FakeController(events=events, **kwargs),
        ),
        patch.object(homekit.ip, "IpPairing", _FakeIpPairing),
        patch.object(
            homekit.zeroconf,
            "async_get_async_instance",
            AsyncMock(return_value=object()),
        ),
        pytest.raises(OSError, match="storage unavailable"),
    ):
        await homekit.async_copy_pairing(
            hass, PAIRING_DATA, on_pairing_saved=fail_to_save
        )

    assert events.index("persist") < events.index("remove_pairing")
    assert "list_accessories" not in events
    assert events[-1] == "stop"
