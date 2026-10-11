"""Shared aiohomekit controller plus pairing helpers."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from typing import Any
import uuid

from aiohomekit import Controller
from aiohomekit.characteristic_cache import CharacteristicCacheMemory

# Import the transports here, in the executor, instead of lazily in the loop.
from aiohomekit.controller import ip
from aiohomekit.exceptions import HomeKitException
from homeassistant.components import zeroconf
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import Event, HomeAssistant
from homeassistant.util.hass_dict import HassKey

from .const import DOMAIN, LOGGER
from .local import (
    LOCAL_ERRORS,
    AccessoryInfo,
    generate_controller_keys,
    parse_accessories,
)

CONTROLLER_KEY: HassKey[Controller] = HassKey(f"{DOMAIN}_controller")
_CONTROLLER_LOCK: HassKey[asyncio.Lock] = HassKey(f"{DOMAIN}_controller_lock")

# Keys of a pairing that change when the thermostat moves on the network.
ENDPOINT_KEYS = ("AccessoryIP", "AccessoryIPs", "AccessoryPort")


async def async_get_controller(hass: HomeAssistant) -> Controller:
    """Return the integration's aiohomekit controller, starting it if needed."""
    if (controller := hass.data.get(CONTROLLER_KEY)) is not None:
        return controller
    lock = hass.data.setdefault(_CONTROLLER_LOCK, asyncio.Lock())
    async with lock:
        if (controller := hass.data.get(CONTROLLER_KEY)) is not None:
            return controller
        async_zc = await zeroconf.async_get_async_instance(hass)
        controller = Controller(
            async_zeroconf_instance=async_zc, char_cache=CharacteristicCacheMemory()
        )
        await controller.async_start()
        hass.data[CONTROLLER_KEY] = controller

        async def _stop(_event: Event) -> None:
            hass.data.pop(CONTROLLER_KEY, None)
            await controller.async_stop()

        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _stop)
        return controller


async def async_copy_pairing(
    hass: HomeAssistant,
    source: Mapping[str, Any],
    *,
    on_pairing_saved: Callable[[Mapping[str, Any]], Awaitable[None]],
) -> tuple[dict[str, Any], AccessoryInfo]:
    """Add a new admin pairing for us, using another controller's pairing.

    This is how a thermostat moves over from the HomeKit Device integration
    without re-entering its setup code. The source pairing keeps working until
    its owner removes it.
    """
    async_zc = await zeroconf.async_get_async_instance(hass)
    controller = Controller(async_zeroconf_instance=async_zc)
    await controller.async_start()
    try:
        old = controller.load_pairing(f"copy-src-{uuid.uuid4()}", dict(source))
        if not isinstance(old, ip.IpPairing):
            raise HomeKitException("Only HomeKit over IP is supported")
        pairing_id, ltsk, ltpk = generate_controller_keys()
        try:
            async with asyncio.timeout(30):
                await old.add_pairing(pairing_id, ltpk, "Admin")
            data = dict(source)
            data.update(
                {
                    "iOSPairingId": pairing_id,
                    "iOSDeviceLTSK": ltsk,
                    "iOSDeviceLTPK": ltpk,
                }
            )
            try:
                # The accessory has accepted our controller key. Save it before
                # the next network request, which can fail after pairing works.
                await asyncio.shield(on_pairing_saved(data))
            except Exception:
                # The caller could not persist our credentials; do not leave a
                # new admin pairing on the accessory where it cannot be reused.
                try:
                    await old.remove_pairing(pairing_id)
                except LOCAL_ERRORS as err:
                    LOGGER.warning("Couldn't roll back copied HomeKit pairing: %s", err)
                raise
        finally:
            try:
                await old.close()
            except LOCAL_ERRORS as err:
                LOGGER.debug("Closing source pairing failed: %s", err)
        new = controller.load_pairing(f"copy-new-{uuid.uuid4()}", dict(data))
        async with asyncio.timeout(30):
            await new.list_accessories_and_characteristics()
        if new.accessories is None:  # pragma: no cover - aiohomekit always sets it
            raise HomeKitException("No accessory data")
        info, _, _ = parse_accessories(new.accessories)
        await new.close()
        return data, info
    finally:
        await controller.async_stop()


async def async_unpair(hass: HomeAssistant, pairing_data: Mapping[str, Any]) -> None:
    """Remove our pairing from the thermostat (best effort)."""
    controller = await async_get_controller(hass)
    alias = f"remove-{uuid.uuid4()}"
    controller.load_pairing(alias, dict(pairing_data))
    try:
        async with asyncio.timeout(15):
            await controller.remove_pairing(alias)
    except LOCAL_ERRORS as err:
        LOGGER.warning(
            "Couldn't remove the HomeKit pairing from the thermostat at %s (%s). "
            "If you want to pair it again, reset its HomeKit pairing on the device",
            pairing_data.get("AccessoryIP"),
            err,
        )
