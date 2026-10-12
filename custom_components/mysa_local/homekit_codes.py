"""Optional private storage for HomeKit setup codes, separate from pairing keys."""

from __future__ import annotations

import asyncio

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.util.hass_dict import HassKey

from .const import DOMAIN

CONF_REMEMBER_HOMEKIT_CODE = "remember_homekit_code"
STORAGE_KEY = f"{DOMAIN}_homekit_codes"


class HomeKitCodes:
    """Serialize changes to codes explicitly remembered by the user."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Create a private HA store; never include its contents in diagnostics."""
        self._store = Store[dict[str, str]](hass, 1, STORAGE_KEY, private=True)
        self._lock = asyncio.Lock()

    async def async_count(self) -> int:
        """Return the number of remembered codes without exposing them."""
        async with self._lock:
            return len(await self._store.async_load() or {})

    async def async_save(self, pairing_id: str, code: str | None) -> None:
        """Remember an accepted code, or forget this accessory's saved code."""
        async with self._lock:
            data = dict(await self._store.async_load() or {})
            key = pairing_id.lower()
            if code is None:
                if key not in data:
                    return
                del data[key]
            else:
                data[key] = code
            await self._store.async_save(data)

    async def async_forget_all(self) -> None:
        """Delete all remembered setup codes, preserving live pairing keys."""
        async with self._lock:
            await self._store.async_save({})


_CODES: HassKey[HomeKitCodes] = HassKey(f"{DOMAIN}_homekit_codes_store")


def homekit_codes(hass: HomeAssistant) -> HomeKitCodes:
    """Return the shared store facade."""
    if (codes := hass.data.get(_CODES)) is None:
        codes = HomeKitCodes(hass)
        hass.data[_CODES] = codes
    return codes
