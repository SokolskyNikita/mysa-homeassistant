"""Local HomeKit (HAP over IP) connection to one Mysa thermostat.

aiohomekit does the protocol work, including reconnecting with backoff. This
class tracks the characteristics a Mysa exposes, pushes changes to listeners and
reports whether the local path is usable right now.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from aiohomekit.exceptions import (
    AccessoryDisconnectedError,
    AccessoryNotFoundError,
    AuthenticationError,
    EncryptionError,
    HomeKitException,
)
from aiohomekit.model.characteristics import CharacteristicsTypes
from aiohomekit.model.services import ServicesTypes
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util

from .const import (
    LOCAL_POLL_INTERVAL,
    LOCAL_READ_TIMEOUT,
    LOGGER,
    MAC_CHAR_UUID,
)

if TYPE_CHECKING:
    from aiohomekit import Controller
    from aiohomekit.controller.abstract import AbstractPairing
    from aiohomekit.model import Accessories

# Errors that mean "the connection is broken", as opposed to "the device refused".
CONNECTION_ERRORS: tuple[type[BaseException], ...] = (
    AccessoryDisconnectedError,
    AccessoryNotFoundError,
    EncryptionError,
    TimeoutError,
    OSError,
)
# Connection errors plus anything else aiohomekit raises.
LOCAL_ERRORS: tuple[type[BaseException], ...] = (*CONNECTION_ERRORS, HomeKitException)

CURRENT_TEMPERATURE = CharacteristicsTypes.TEMPERATURE_CURRENT
TARGET_TEMPERATURE = CharacteristicsTypes.TEMPERATURE_TARGET
CURRENT_STATE = CharacteristicsTypes.HEATING_COOLING_CURRENT
TARGET_STATE = CharacteristicsTypes.HEATING_COOLING_TARGET
DISPLAY_UNITS = CharacteristicsTypes.TEMPERATURE_UNITS
IDENTIFY = CharacteristicsTypes.IDENTIFY

_THERMOSTAT_CHARS = (
    CURRENT_TEMPERATURE,
    TARGET_TEMPERATURE,
    CURRENT_STATE,
    TARGET_STATE,
    DISPLAY_UNITS,
)
_INFO_CHARS = {
    CharacteristicsTypes.NAME: "name",
    CharacteristicsTypes.MODEL: "model",
    CharacteristicsTypes.MANUFACTURER: "manufacturer",
    CharacteristicsTypes.SERIAL_NUMBER: "serial",
    CharacteristicsTypes.FIRMWARE_REVISION: "firmware",
    MAC_CHAR_UUID: "mac",
}


class LocalWriteRejectedError(HomeKitException):
    """The thermostat answered but refused the write (bad value, read-only...)."""


@dataclass(slots=True)
class CharMeta:
    """Where a characteristic lives and what values it allows."""

    aid: int
    iid: int
    min_value: float | None
    max_value: float | None
    min_step: float | None
    valid_values: tuple[int, ...] | None


@dataclass(slots=True)
class AccessoryInfo:
    """Accessory-information strings read from the thermostat."""

    name: str | None = None
    model: str | None = None
    manufacturer: str | None = None
    serial: str | None = None
    firmware: str | None = None
    mac: str | None = None

    @property
    def device_id(self) -> str | None:
        """Mysa's cloud device ID: the Wi-Fi MAC, lowercase, no separators."""
        if not self.mac:
            return None
        mac = "".join(ch for ch in self.mac.lower() if ch in "0123456789abcdef")
        return mac if len(mac) == 12 else None


def parse_accessories(
    accessories: Accessories,
) -> tuple[AccessoryInfo, dict[str, CharMeta], dict[str, Any]]:
    """Find the info strings, thermostat characteristics and current values."""
    info = AccessoryInfo()
    chars: dict[str, CharMeta] = {}
    values: dict[str, Any] = {}
    for accessory in accessories:
        for service in accessory.services:
            stype = service.type
            for char in service.characteristics:
                ctype = char.type
                if stype == ServicesTypes.ACCESSORY_INFORMATION:
                    if ctype in _INFO_CHARS:
                        value = char.value
                        if isinstance(value, str) and value:
                            setattr(info, _INFO_CHARS[ctype], value.strip().strip('"'))
                    elif ctype == IDENTIFY and IDENTIFY not in chars:
                        chars[IDENTIFY] = CharMeta(
                            accessory.aid, char.iid, None, None, None, None
                        )
                    continue
                if stype != ServicesTypes.THERMOSTAT or ctype not in _THERMOSTAT_CHARS:
                    continue
                valid = char.valid_values
                chars[ctype] = CharMeta(
                    aid=accessory.aid,
                    iid=char.iid,
                    min_value=char.minValue,
                    max_value=char.maxValue,
                    min_step=char.minStep,
                    valid_values=tuple(valid) if valid else None,
                )
                values[ctype] = char.value
    return info, chars, values


class LocalThermostat:
    """The HomeKit side of one thermostat."""

    def __init__(
        self,
        hass: HomeAssistant,
        controller: Controller,
        pairing_data: Mapping[str, Any],
        name: str,
    ) -> None:
        """Load the pairing; nothing touches the network until async_start."""
        self.hass = hass
        self.name = name
        self.pairing_data = dict(pairing_data)
        self.hkid: str = self.pairing_data["AccessoryPairingID"]
        self.pairing: AbstractPairing = controller.load_pairing(
            self.hkid, dict(self.pairing_data)
        )
        self.info = AccessoryInfo()
        self.chars: dict[str, CharMeta] = {}
        self.values: dict[str, Any] = {}
        self.available = False
        self.down_since: datetime | None = dt_util.utcnow()
        self.last_error: str | None = None
        self._listeners: set[CALLBACK_TYPE] = set()
        self._unsubs: list[CALLBACK_TYPE] = []
        self._poll_lock = asyncio.Lock()
        self._started = False

    @property
    def host(self) -> str | None:
        """The IP the pairing last used."""
        return self.pairing_data.get("AccessoryIP")

    @callback
    def add_listener(self, listener: CALLBACK_TYPE) -> CALLBACK_TYPE:
        """Call `listener` whenever a value or the availability changes."""
        self._listeners.add(listener)
        return lambda: self._listeners.discard(listener)

    @callback
    def _notify(self) -> None:
        for listener in list(self._listeners):
            listener()

    @callback
    def _set_available(self, available: bool, error: str | None = None) -> None:
        if available:
            self.last_error = None
        elif error:
            self.last_error = error
        if available == self.available:
            return
        self.available = available
        self.down_since = None if available else dt_util.utcnow()
        LOGGER.debug(
            "%s: local HomeKit connection %s%s",
            self.name,
            "up" if available else "down",
            f" ({error})" if error else "",
        )
        self._notify()

    async def async_start(self) -> None:
        """Connect, read the accessory database and subscribe to changes.

        Never raises for connection problems: the thermostat just starts out
        unavailable and the background poll keeps trying.
        """
        self._started = True
        self._unsubs.append(self.pairing.dispatcher_connect(self._handle_events))
        self._unsubs.append(
            async_track_time_interval(
                self.hass,
                self._async_scheduled_poll,
                LOCAL_POLL_INTERVAL,
                name=f"Mysa {self.name} local poll",
            )
        )
        await self._async_connect()

    async def _async_connect(self) -> bool:
        """Fetch the accessory database if we don't have it yet."""
        try:
            async with asyncio.timeout(15):
                await self.pairing.async_populate_accessories_state(force_update=True)
        except AuthenticationError as err:
            # The thermostat no longer knows this pairing (reset or removed).
            self._set_available(False, f"pairing rejected: {err}")
            return False
        except LOCAL_ERRORS as err:
            self._set_available(False, str(err) or type(err).__name__)
            return False
        accessories = self.pairing.accessories
        if accessories is None:
            self._set_available(False, "no accessory data")
            return False
        self.info, self.chars, values = parse_accessories(accessories)
        self.values.update(values)
        watch = [
            (meta.aid, meta.iid)
            for ctype, meta in self.chars.items()
            if ctype != IDENTIFY
        ]
        if watch:
            try:
                await self.pairing.subscribe(watch)
            except LOCAL_ERRORS as err:
                LOGGER.debug("%s: subscribe failed: %s", self.name, err)
        self._set_available(True)
        self._notify()
        return True

    async def _async_scheduled_poll(self, _now: datetime | None = None) -> None:
        await self.async_poll()

    async def async_poll(self) -> None:
        """Read the thermostat characteristics (also our reconnect probe)."""
        if self._poll_lock.locked():
            return
        async with self._poll_lock:
            if not self.chars:
                await self._async_connect()
                return
            try:
                values = await self.async_read(
                    [c for c in _THERMOSTAT_CHARS if c in self.chars],
                    limit=LOCAL_READ_TIMEOUT * 3,
                )
            except LOCAL_ERRORS as err:
                self._set_available(False, str(err) or type(err).__name__)
                return
            changed = any(self.values.get(k) != v for k, v in values.items())
            self.values.update(values)
            self._set_available(True)
            if changed:
                self._notify()

    @callback
    def _handle_events(
        self, events: Mapping[tuple[int, int], Mapping[str, Any]]
    ) -> None:
        """Values pushed by the thermostat (or echoed after our own writes).

        An empty dict means the connection was re-established.
        """
        if events and (accessories := self.pairing.accessories) is not None:
            # Keep aiohomekit's cached model in step, like homekit_controller.
            accessories.process_changes({k: dict(v) for k, v in events.items()})
        by_iid = {(m.aid, m.iid): ctype for ctype, m in self.chars.items()}
        changed = False
        for aid_iid, payload in events.items():
            ctype = by_iid.get(aid_iid)
            if ctype is None or "value" not in payload:
                continue
            if self.values.get(ctype) != payload["value"]:
                self.values[ctype] = payload["value"]
                changed = True
        if self.chars:
            if not self.available:
                self._set_available(True)
                return
            if changed:
                self._notify()

    async def async_read(
        self, ctypes: Iterable[str], *, limit: float | None = None
    ) -> dict[str, Any]:
        """Read characteristics now. Raises CONNECTION_ERRORS on failure."""
        wanted = {
            (self.chars[c].aid, self.chars[c].iid): c for c in ctypes if c in self.chars
        }
        if not wanted:
            return {}
        async with asyncio.timeout(limit or LOCAL_READ_TIMEOUT):
            result = await self.pairing.get_characteristics(list(wanted))
        return {
            wanted[aid_iid]: payload["value"]
            for aid_iid, payload in result.items()
            if aid_iid in wanted and "value" in payload
        }

    def start_write(self, values: Mapping[str, Any]) -> asyncio.Task[None]:
        """Start writing characteristics; the caller decides how long to wait.

        The task is never cancelled by the caller, so a slow write can't leave
        the HAP session half-way through a request.
        """
        payload = [
            (self.chars[ctype].aid, self.chars[ctype].iid, value)
            for ctype, value in values.items()
        ]

        async def _write() -> None:
            result = await self.pairing.put_characteristics(payload)
            failed = {k: v for k, v in (result or {}).items() if v.get("status", 0)}
            if failed:
                raise LocalWriteRejectedError(f"Write refused: {failed}")
            self.values.update(values)

        return self.hass.async_create_background_task(
            _write(), f"mysa {self.name} local write"
        )

    @callback
    def mark_available(self) -> None:
        """Called by the router when a read-back proved the connection works."""
        self._set_available(True)

    @callback
    def mark_unavailable(self, error: str) -> None:
        """Called by the router when a write showed the connection is gone."""
        self._set_available(False, error)

    async def async_identify(self) -> None:
        """Make the thermostat identify itself."""
        meta = self.chars.get(IDENTIFY)
        if meta is None:
            await self.pairing.identify()
            return
        async with asyncio.timeout(LOCAL_READ_TIMEOUT * 2):
            await self.pairing.put_characteristics([(meta.aid, meta.iid, True)])

    async def async_stop(self) -> None:
        """Disconnect and stop polling."""
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        self._listeners.clear()
        if self._started:
            try:
                async with asyncio.timeout(5):
                    await self.pairing.close()
            except LOCAL_ERRORS as err:
                LOGGER.debug("%s: error while closing: %s", self.name, err)

    def down_for(self) -> timedelta | None:
        """How long the local path has been down (None while it's up)."""
        if self.available or self.down_since is None:
            return None
        return dt_util.utcnow() - self.down_since


def generate_controller_keys() -> tuple[str, str, str]:
    """Create a fresh HomeKit controller identity: (pairing id, LTSK hex, LTPK hex)."""
    import uuid

    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ed25519

    key = ed25519.Ed25519PrivateKey.generate()
    ltsk = key.private_bytes(
        serialization.Encoding.Raw,
        serialization.PrivateFormat.Raw,
        serialization.NoEncryption(),
    ).hex()
    ltpk = (
        key.public_key()
        .public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        .hex()
    )
    return str(uuid.uuid4()), ltsk, ltpk


def merge_callbacks(*unsubs: Callable[[], None]) -> Callable[[], None]:
    """Combine several unsubscribe callbacks into one."""

    def _unsub() -> None:
        for unsub in unsubs:
            unsub()

    return _unsub
