"""Durable storage for HomeKit pairings accepted during setup flows."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any
import uuid

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.util.hass_dict import HassKey

from .const import DOMAIN

PENDING_PAIRINGS_STORAGE_KEY = f"{DOMAIN}_pending_pairings"
_PENDING_PAIRINGS_STORE: HassKey[Store[dict[str, dict[str, Any]]]] = HassKey(
    f"{DOMAIN}_pending_pairings_store"
)
_PENDING_PAIRINGS_LOCK: HassKey[asyncio.Lock] = HassKey(
    f"{DOMAIN}_pending_pairings_lock"
)
_PAIRING_SESSION: HassKey[str] = HassKey(f"{DOMAIN}_pairing_session")


class PairingStore:
    """Persist pairings until Home Assistant has saved their config entry.

    HomeKit pairing changes device state immediately. Keeping a private backup
    lets setup resume after an interrupted flow or restart without asking for
    the setup code again.
    """

    def __init__(self, hass: HomeAssistant) -> None:
        """Bind the store facade to one HA instance."""
        self._hass = hass

    @property
    def session_id(self) -> str:
        """Return an ID stable for this HA process and new after a restart."""
        if session_id := self._hass.data.get(_PAIRING_SESSION):
            return session_id
        session_id = str(uuid.uuid4())
        self._hass.data[_PAIRING_SESSION] = session_id
        return session_id

    def _store(self) -> Store[dict[str, dict[str, Any]]]:
        if store := self._hass.data.get(_PENDING_PAIRINGS_STORE):
            return store
        store = Store(self._hass, 1, PENDING_PAIRINGS_STORAGE_KEY, private=True)
        self._hass.data[_PENDING_PAIRINGS_STORE] = store
        return store

    def _lock(self) -> asyncio.Lock:
        if lock := self._hass.data.get(_PENDING_PAIRINGS_LOCK):
            return lock
        lock = asyncio.Lock()
        self._hass.data[_PENDING_PAIRINGS_LOCK] = lock
        return lock

    async def async_load(self) -> dict[str, dict[str, Any]]:
        """Load valid recovery records, returning a copy safe for callers."""
        async with self._lock():
            data = await self._store().async_load()
            if not isinstance(data, dict):
                return {}
            return {
                hkid: dict(record)
                for hkid, record in data.items()
                if isinstance(hkid, str) and isinstance(record, dict)
            }

    async def async_save(
        self,
        hkid: str,
        pairing: Mapping[str, Any],
        *,
        name: str | None,
        model: str | None,
        migrate_from: str | None = None,
    ) -> None:
        """Persist pairing credentials and the context needed to resume setup."""
        async with self._lock():
            store = self._store()
            loaded = await store.async_load()
            data = dict(loaded) if isinstance(loaded, dict) else {}
            data[hkid.lower()] = {
                "pairing": dict(pairing),
                "name": name,
                "model": model,
                "migrate_from": migrate_from,
                "session": self.session_id,
            }
            await store.async_save(data)

    async def async_clear(self, hkid: str) -> None:
        """Remove a recovery record after the accessory is reset or rejected it."""
        async with self._lock():
            store = self._store()
            loaded = await store.async_load()
            data = dict(loaded) if isinstance(loaded, dict) else {}
            if hkid.lower() not in data:
                return
            data.pop(hkid.lower())
            if data:
                await store.async_save(data)
            else:
                await store.async_remove()

    async def async_clear_configured_backups(self, configured_hkids: set[str]) -> None:
        """Prune backups for already-configured pairings from older HA sessions.

        A backup created during the current process remains until HA starts,
        because the corresponding config entry might still be in progress.
        """
        configured = {hkid.lower() for hkid in configured_hkids}
        async with self._lock():
            store = self._store()
            loaded = await store.async_load()
            data = dict(loaded) if isinstance(loaded, dict) else {}
            remaining = {
                hkid: record
                for hkid, record in data.items()
                if hkid.lower() not in configured
                or (
                    isinstance(record, dict)
                    and record.get("session") == self.session_id
                )
            }
            if remaining == data:
                return
            if remaining:
                await store.async_save(remaining)
            else:
                await store.async_remove()


_PAIRING_STORE_SERVICE: HassKey[PairingStore] = HassKey(
    f"{DOMAIN}_pairing_store_service"
)


def pairing_store(hass: HomeAssistant) -> PairingStore:
    """Return the integration's process-local pairing store facade."""
    if store := hass.data.get(_PAIRING_STORE_SERVICE):
        return store
    store = PairingStore(hass)
    hass.data[_PAIRING_STORE_SERVICE] = store
    return store


def pairing_session_id(hass: HomeAssistant) -> str:
    """Return the stable session ID used to decide which backups can be pruned."""
    return pairing_store(hass).session_id


async def async_load_pending_pairings(
    hass: HomeAssistant,
) -> dict[str, dict[str, Any]]:
    """Load HomeKit pairings accepted but not yet persisted in an entry."""
    return await pairing_store(hass).async_load()


async def async_clear_configured_pairing_backups(
    hass: HomeAssistant, configured_hkids: set[str]
) -> None:
    """Prune stale backups after Home Assistant has loaded config entries."""
    await pairing_store(hass).async_clear_configured_backups(configured_hkids)
