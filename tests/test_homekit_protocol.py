"""Behavioral tests for the isolated HomeKit protocol boundary."""

import asyncio
from collections.abc import Mapping
from typing import Any
from unittest.mock import AsyncMock, patch

from aiohomekit.model.characteristics import CharacteristicsTypes
import pytest

from custom_components.mysa_local.protocols.homekit import (
    Field,
    HomeKitCharacteristicReadError,
    HomeKitProtocol,
    HomeKitWriteRejectedError,
    LocalAction,
    LocalMode,
    PairingSession,
    pairing_identity,
    parse_advertisement,
    update_pairing_endpoint,
)

from .common import PAIRING_DATA, fake_characteristic_address, make_controller


async def test_device_reads_and_writes_semantic_values() -> None:
    """Wire enums and characteristic addresses stay behind HomeKitDevice."""
    controller = await make_controller(paired=True)
    protocol = HomeKitProtocol.from_controller(controller)
    device = protocol.load_pairing(PAIRING_DATA)
    events = []
    unsubscribe = device.add_listener(events.append)

    await device.async_connect()

    assert device.ready
    assert device.state[Field.CURRENT_TEMPERATURE] == pytest.approx(21.4)
    assert device.state[Field.TARGET_TEMPERATURE] == pytest.approx(21.5)
    assert device.state[Field.CURRENT_ACTION] == LocalAction.HEATING
    assert device.state[Field.TARGET_MODE] == LocalMode.HEAT
    assert device.state[Field.DISPLAY_UNIT] == "C"
    assert device.supported_modes == (LocalMode.OFF, LocalMode.HEAT)
    assert device.temperature_range == (5, 30, 0.5)

    await device.async_write(
        {
            Field.TARGET_MODE: LocalMode.OFF,
            Field.TARGET_TEMPERATURE: 19.5,
            Field.DISPLAY_UNIT: "F",
        }
    )

    assert device.state[Field.TARGET_MODE] == LocalMode.OFF
    assert device.state[Field.TARGET_TEMPERATURE] == pytest.approx(19.5)
    assert device.state[Field.DISPLAY_UNIT] == "F"
    assert events[-1].values == {
        Field.TARGET_MODE: LocalMode.OFF,
        Field.TARGET_TEMPERATURE: 19.5,
        Field.DISPLAY_UNIT: "F",
    }
    diagnostics = device.diagnostics()
    assert diagnostics["fields"][Field.TARGET_MODE.value]["valid_values"] == (
        LocalMode.OFF,
        LocalMode.HEAT,
    )
    assert diagnostics["fields"][Field.TARGET_MODE.value]["min_value"] is None
    assert "AccessoryPairingID" not in str(diagnostics)
    assert "serial" not in diagnostics
    unsubscribe()
    await device.async_close()


async def test_unknown_wire_enums_are_not_exposed() -> None:
    """Unknown HAP enum values become unknown semantic state, never raw ints."""
    controller = await make_controller(paired=True)
    pairing = controller.aliases[PAIRING_DATA["AccessoryPairingID"]]
    device = HomeKitProtocol.from_controller(controller).load_pairing(PAIRING_DATA)
    await device.async_connect()
    target_address = fake_characteristic_address(
        pairing, CharacteristicsTypes.HEATING_COOLING_TARGET
    )
    display_address = fake_characteristic_address(
        pairing, CharacteristicsTypes.TEMPERATURE_UNITS
    )

    async def invalid_values(
        _addresses: list[tuple[int, int]],
    ) -> Mapping[tuple[int, int], dict[str, Any]]:
        return {
            target_address: {"value": 99},
            display_address: {"value": 99},
        }

    with patch.object(
        pairing, "get_characteristics", new=AsyncMock(side_effect=invalid_values)
    ):
        values = await device.async_read((Field.TARGET_MODE, Field.DISPLAY_UNIT))

    assert values == {Field.TARGET_MODE: None, Field.DISPLAY_UNIT: None}
    assert device.state[Field.TARGET_MODE] is None
    assert device.state[Field.DISPLAY_UNIT] is None
    with pytest.raises(HomeKitWriteRejectedError):
        await device.async_write({Field.TARGET_MODE: "mystery"})
    await device.async_close()


async def test_required_read_failures_report_semantic_fields() -> None:
    """The protocol reports missing values without exposing aid/iid pairs."""
    controller = await make_controller(paired=True)
    pairing = controller.aliases[PAIRING_DATA["AccessoryPairingID"]]
    device = HomeKitProtocol.from_controller(controller).load_pairing(PAIRING_DATA)
    await device.async_connect()
    target_address = fake_characteristic_address(
        pairing, CharacteristicsTypes.TEMPERATURE_TARGET
    )

    with (
        patch.object(
            pairing,
            "get_characteristics",
            new=AsyncMock(return_value={target_address: {"status": -70402}}),
        ),
        pytest.raises(HomeKitCharacteristicReadError) as error,
    ):
        await device.async_read((Field.TARGET_TEMPERATURE,))

    assert error.value.fields == {Field.TARGET_TEMPERATURE}
    assert str(target_address) not in str(error.value)
    await device.async_close()


def test_advertisement_and_pairing_helpers_hide_txt_and_endpoint_keys() -> None:
    """Advertisement text and endpoint key names are protocol details."""
    advertisement = parse_advertisement(
        "Mysa-d4e5f6._hap._tcp.local.",
        {b"id": b"AA:BB:CC:DD:EE:FF", b"md": b"BB-V2-0-L", b"sf": b"0"},
        host="192.0.2.9",
        addresses=("192.0.2.9", "fe80::1"),
        port=57959,
    )

    assert advertisement is not None
    assert advertisement.id == "aa:bb:cc:dd:ee:ff"
    assert advertisement.name == "Mysa-d4e5f6"
    assert advertisement.paired
    assert advertisement.endpoint.addresses == ("192.0.2.9",)
    updated = update_pairing_endpoint(PAIRING_DATA, advertisement.endpoint)
    assert updated["AccessoryIP"] == "192.0.2.9"
    assert updated["AccessoryPort"] == 57959
    assert pairing_identity(updated) == pairing_identity(PAIRING_DATA)
    assert "AccessoryIP" not in pairing_identity(updated)


async def test_pairing_persists_before_accessory_info_read() -> None:
    """The callback durably records accepted keys before another HAP request."""
    controller = await make_controller(paired=True)
    pairing = controller.aliases[PAIRING_DATA["AccessoryPairingID"]]
    events: list[str] = []
    original_list = pairing.list_accessories_and_characteristics

    async def list_accessories() -> Any:
        events.append("read")
        return await original_list()

    async def save(_pairing: Mapping[str, Any]) -> None:
        events.append("save")
        assert events == ["save"]

    with patch.object(
        pairing, "list_accessories_and_characteristics", list_accessories
    ):
        session = PairingSession(lambda _pin: _return(pairing))
        data, info = await session.async_complete("111-22-333", on_pairing_saved=save)

    assert events == ["save", "read"]
    assert data == PAIRING_DATA
    assert info.model == "BB-V2-0-L"


async def test_pairing_storage_error_rolls_back_and_preserves_original_error() -> None:
    """Failed credential storage removes the accepted key before returning."""
    controller = await make_controller(paired=True)
    pairing = controller.aliases[PAIRING_DATA["AccessoryPairingID"]]
    removed: list[str] = []

    async def remove_pairing(pairing_id: str) -> None:
        removed.append(pairing_id)

    async def fail_to_save(_pairing: Mapping[str, Any]) -> None:
        raise OSError("storage is read-only")

    with patch.object(pairing, "remove_pairing", remove_pairing):
        session = PairingSession(lambda _pin: _return(pairing))
        with pytest.raises(OSError, match="storage is read-only"):
            await session.async_complete("111-22-333", on_pairing_saved=fail_to_save)

    assert removed == [PAIRING_DATA["iOSPairingId"]]


async def test_pairing_storage_callback_is_shielded_from_cancellation() -> None:
    """A cancellation waits for persistence and does not start accessory reads."""
    controller = await make_controller(paired=True)
    pairing = controller.aliases[PAIRING_DATA["AccessoryPairingID"]]
    save_started = asyncio.Event()
    release_save = asyncio.Event()
    reads: list[str] = []
    original_list = pairing.list_accessories_and_characteristics

    async def list_accessories() -> Any:
        reads.append("read")
        return await original_list()

    async def save(_pairing: Mapping[str, Any]) -> None:
        save_started.set()
        await release_save.wait()

    with patch.object(
        pairing, "list_accessories_and_characteristics", list_accessories
    ):
        session = PairingSession(lambda _pin: _return(pairing))
        task = asyncio.create_task(
            session.async_complete("111-22-333", on_pairing_saved=save)
        )
        await save_started.wait()
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        release_save.set()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert reads == []


async def _return(value: Any) -> Any:
    return value
