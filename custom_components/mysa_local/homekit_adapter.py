"""Home Assistant lifecycle helpers for the HomeKit protocol package."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from homeassistant.components import zeroconf
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import Event, HomeAssistant
from homeassistant.util.hass_dict import HassKey
from mysa_client.homekit import (
    AccessoryInfo,
    HomeKitAdvertisement,
    HomeKitError,
    HomeKitProtocol,
    parse_advertisement,
    update_pairing_endpoint,
)

from .const import DOMAIN, LOGGER

_PROTOCOL_KEY: HassKey[HomeKitProtocol] = HassKey(f"{DOMAIN}_homekit_protocol")
_PROTOCOL_LOCK: HassKey[asyncio.Lock] = HassKey(f"{DOMAIN}_homekit_protocol_lock")


def discovery_from_zeroconf(info: Any) -> HomeKitAdvertisement | None:
    """Convert HA's mDNS record into a protocol-level Mysa advertisement."""
    return parse_advertisement(
        info.name,
        info.properties,
        host=info.host,
        addresses=getattr(info, "ip_addresses", ()),
        port=info.port,
    )


def update_pairing_endpoints(
    pairing_data: Mapping[str, Any], advertisement: HomeKitAdvertisement
) -> dict[str, Any]:
    """Update opaque pairing credentials from a discovered endpoint."""
    return update_pairing_endpoint(pairing_data, advertisement.endpoint)


async def async_get_controller(hass: HomeAssistant) -> HomeKitProtocol:
    """Return the shared semantic HomeKit facade, starting it if needed."""
    if (protocol := hass.data.get(_PROTOCOL_KEY)) is not None:
        return protocol
    lock = hass.data.setdefault(_PROTOCOL_LOCK, asyncio.Lock())
    async with lock:
        if (protocol := hass.data.get(_PROTOCOL_KEY)) is not None:
            return protocol
        async_zeroconf = await zeroconf.async_get_async_instance(hass)
        protocol = HomeKitProtocol(async_zeroconf)
        await protocol.async_start()
        hass.data[_PROTOCOL_KEY] = protocol

        async def _stop(_event: Event) -> None:
            hass.data.pop(_PROTOCOL_KEY, None)
            try:
                await protocol.async_stop()
            except HomeKitError as error:
                LOGGER.debug(
                    "Stopping the HomeKit controller failed: %s", type(error).__name__
                )

        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _stop)
        return protocol


async def async_copy_pairing(
    hass: HomeAssistant,
    source: Mapping[str, Any],
    *,
    on_pairing_saved: Callable[[Mapping[str, Any]], Awaitable[None]],
) -> tuple[dict[str, Any], AccessoryInfo]:
    """Create our own pairing through an existing HomeKit Device entry."""
    protocol = await async_get_controller(hass)
    return await protocol.async_copy_pairing(source, on_pairing_saved=on_pairing_saved)


async def async_unpair(hass: HomeAssistant, pairing_data: Mapping[str, Any]) -> None:
    """Remove our HomeKit key from the accessory using the shared controller."""
    protocol = await async_get_controller(hass)
    try:
        async with asyncio.timeout(15):
            await protocol.async_unpair(pairing_data)
    except (HomeKitError, TimeoutError) as error:
        LOGGER.warning(
            "Couldn't remove the HomeKit pairing from the thermostat (%s). "
            "If you want to pair it again, reset its HomeKit pairing on the device",
            type(error).__name__,
        )
