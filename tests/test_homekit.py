"""Tests for Home Assistant's HomeKit lifecycle and pairing adapter."""

from unittest.mock import AsyncMock, patch

from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import HomeAssistant
from mysa_client.homekit import AccessoryInfo, HomeKitError

from custom_components.mysa_local import homekit_adapter

from .common import PAIRING_DATA


async def test_async_get_controller_is_shared_and_stopped_with_hass(
    hass: HomeAssistant,
) -> None:
    """One protocol instance is started once and closed with Home Assistant."""
    protocol = AsyncMock()
    zeroconf_instance = object()

    with (
        patch.object(
            homekit_adapter.zeroconf,
            "async_get_async_instance",
            AsyncMock(return_value=zeroconf_instance),
        ) as get_zeroconf,
        patch.object(homekit_adapter, "HomeKitProtocol", return_value=protocol),
    ):
        first = await homekit_adapter.async_get_controller(hass)
        second = await homekit_adapter.async_get_controller(hass)

        assert first is protocol
        assert second is protocol
        protocol.async_start.assert_awaited_once_with()
        get_zeroconf.assert_awaited_once_with(hass)

        hass.bus.async_fire(EVENT_HOMEASSISTANT_STOP)
        await hass.async_block_till_done()

    protocol.async_stop.assert_awaited_once_with()


async def test_pairing_helpers_delegate_to_protocol(
    hass: HomeAssistant,
) -> None:
    """HA helpers pass through the protocol's semantic pairing operations."""
    protocol = AsyncMock()
    expected = (dict(PAIRING_DATA), AccessoryInfo(model="BB-V2-0-L"))
    protocol.async_copy_pairing.return_value = expected
    save_pairing = AsyncMock()

    with patch.object(
        homekit_adapter,
        "async_get_controller",
        AsyncMock(return_value=protocol),
    ):
        result = await homekit_adapter.async_copy_pairing(
            hass, PAIRING_DATA, on_pairing_saved=save_pairing
        )
        await homekit_adapter.async_unpair(hass, PAIRING_DATA)

    assert result == expected
    protocol.async_copy_pairing.assert_awaited_once_with(
        PAIRING_DATA, on_pairing_saved=save_pairing
    )
    protocol.async_unpair.assert_awaited_once_with(PAIRING_DATA)


async def test_unpair_swallows_protocol_failure(hass: HomeAssistant) -> None:
    """A failed accessory-side unpair leaves HA cleanup able to complete."""
    protocol = AsyncMock()
    protocol.async_unpair.side_effect = HomeKitError("device unavailable")

    with patch.object(
        homekit_adapter,
        "async_get_controller",
        AsyncMock(return_value=protocol),
    ):
        await homekit_adapter.async_unpair(hass, PAIRING_DATA)

    protocol.async_unpair.assert_awaited_once_with(PAIRING_DATA)
