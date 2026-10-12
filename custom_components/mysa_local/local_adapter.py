"""Home Assistant lifecycle adapter for one semantic HomeKit device."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterable, Mapping
from datetime import datetime, timedelta
from typing import Any

from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util
from mysa_client.homekit import (
    AccessoryInfo,
    CharacteristicInfo,
    Field,
    HomeKitConnectionError,
    HomeKitDevice,
    HomeKitError,
    HomeKitEvent,
    HomeKitProtocol,
    HomeKitWriteRejectedError,
    LocalMode,
)

from .const import LOCAL_POLL_INTERVAL, LOCAL_READ_TIMEOUT, LOGGER

# Keep the public aliases semantic. HomeKit UUIDs, IIDs and integer enum values
# belong exclusively to mysa_client.homekit.
CURRENT_TEMPERATURE = Field.CURRENT_TEMPERATURE
TARGET_TEMPERATURE = Field.TARGET_TEMPERATURE
CURRENT_STATE = Field.CURRENT_ACTION
TARGET_STATE = Field.TARGET_MODE
DISPLAY_UNITS = Field.DISPLAY_UNIT

_ESSENTIAL_FIELDS = frozenset(
    {
        CURRENT_TEMPERATURE,
        TARGET_TEMPERATURE,
        CURRENT_STATE,
        TARGET_STATE,
    }
)

CONNECTION_ERRORS: tuple[type[BaseException], ...] = (
    HomeKitConnectionError,
    TimeoutError,
    OSError,
)
LOCAL_ERRORS: tuple[type[BaseException], ...] = (
    HomeKitError,
    TimeoutError,
    OSError,
)
LocalWriteRejectedError = HomeKitWriteRejectedError


class LocalThermostat:
    """Bridge a semantic HomeKit device into HA timers and callbacks."""

    def __init__(
        self,
        hass: HomeAssistant,
        controller: HomeKitProtocol,
        pairing_data: Mapping[str, Any],
        name: str,
    ) -> None:
        """Load a pairing without connecting to the accessory yet."""
        self.hass = hass
        self.name = name
        self.__device: HomeKitDevice = controller.load_pairing(pairing_data)
        self.__listeners: set[CALLBACK_TYPE] = set()
        self.__capability_listeners: set[CALLBACK_TYPE] = set()
        self.__refresh_task: asyncio.Task[None] | None = None
        self.__device_unsub: Callable[[], None] | None = None
        self.__poll_unsub: CALLBACK_TYPE | None = None
        self.__started = False
        self.__poll_lock = asyncio.Lock()
        self.available = False
        self.down_since: datetime | None = dt_util.utcnow()
        self.last_error: str | None = None

    @property
    def pairing_data(self) -> dict[str, Any]:
        """Return the opaque credentials and latest endpoint as a copy."""
        return self.__device.pairing_data

    @property
    def host(self) -> str | None:
        """Return the protocol's last known address for repair messaging."""
        return self.__device.host

    @property
    def info(self) -> AccessoryInfo:
        """Accessory identity read from the HomeKit information service."""
        return self.__device.info

    @property
    def values(self) -> Mapping[Field, Any]:
        """Immutable current semantic characteristic values."""
        return self.__device.state

    @property
    def chars(self) -> Mapping[Field, CharacteristicInfo]:
        """Immutable metadata for the semantic fields this device supports."""
        return self.__device.capabilities

    @property
    def ready(self) -> bool:
        """Whether the essential thermostat fields are present."""
        return self.__device.ready

    @property
    def supported_modes(self) -> tuple[LocalMode, ...]:
        """Modes advertised by this accessory."""
        return self.__device.supported_modes

    @property
    def temperature_range(self) -> tuple[float | None, float | None, float | None]:
        """Setpoint minimum, maximum and step."""
        return self.__device.temperature_range

    @callback
    def supports(self, field: Field) -> bool:
        """Whether a semantic field is present in the accessory database."""
        return field in self.__device.supported_fields

    @callback
    def add_listener(self, listener: CALLBACK_TYPE) -> CALLBACK_TYPE:
        """Call the listener when values or availability change."""
        self.__listeners.add(listener)
        return lambda: self.__listeners.discard(listener)

    @callback
    def add_capability_listener(self, listener: CALLBACK_TYPE) -> CALLBACK_TYPE:
        """Follow local field discovery and advertised metadata changes."""
        self.__capability_listeners.add(listener)
        return lambda: self.__capability_listeners.discard(listener)

    @callback
    def __notify(self) -> None:
        for listener in tuple(self.__listeners):
            listener()

    @callback
    def __set_available(self, available: bool, error: str | None = None) -> None:
        if available:
            self.last_error = None
        elif error:
            self.last_error = error
        if available == self.available:
            return
        self.available = available
        self.down_since = None if available else dt_util.utcnow()
        LOGGER.debug("Local HomeKit connection %s", "up" if available else "down")
        self.__notify()

    @callback
    def __handle_event(self, event: HomeKitEvent) -> None:
        """Apply translated notifications without inspecting HAP payloads."""
        if event.refresh_required:
            if self.__started and (
                self.__refresh_task is None or self.__refresh_task.done()
            ):
                self.__refresh_task = self.hass.async_create_background_task(
                    self.__async_refresh(), f"mysa {self.name} metadata refresh"
                )
            return
        if event.capabilities_changed:
            for listener in tuple(self.__capability_listeners):
                listener()
            if not self.ready:
                self.__set_available(False, "HomeKit missing required fields")
        essential_error = any(
            field in _ESSENTIAL_FIELDS for field in event.failed_fields
        )
        if essential_error:
            self.__set_available(False, self.__format_event_error(event))
            return

        if (
            self.ready
            and not (_ESSENTIAL_FIELDS & self.__device.failed_fields.keys())
            and (
                event.reconnected
                or any(field in _ESSENTIAL_FIELDS for field in event.values)
            )
        ):
            self.__set_available(True)
        if (event.changed_fields and self.available) or event.capabilities_changed:
            self.__notify()

    async def __async_refresh(self) -> None:
        # Wait for an in-flight poll before checking metadata; dropping this
        # request could leave a configuration event unhandled until the next poll.
        async with self.__poll_lock:
            pass
        await self.async_poll()

    @staticmethod
    def __format_event_error(event: HomeKitEvent) -> str:
        return (
            ", ".join(
                f"{field.value}: {status}"
                for field, status in event.failed_fields.items()
            )
            or "HomeKit characteristic read failed"
        )

    async def async_start(self) -> None:
        """Connect, subscribe and schedule background polling."""
        if self.__started:
            return
        self.__started = True
        self.__device_unsub = self.__device.add_listener(self.__handle_event)
        self.__poll_unsub = async_track_time_interval(
            self.hass,
            self._async_scheduled_poll,
            LOCAL_POLL_INTERVAL,
            name=f"Mysa {self.name} local poll",
        )
        try:
            async with asyncio.timeout(15):
                await self.__device.async_connect()
        except (HomeKitError, TimeoutError) as error:
            self.__set_available(False, str(error) or type(error).__name__)
            return
        if not self.ready:
            missing = _ESSENTIAL_FIELDS - self.__device.supported_fields
            self.__set_available(
                False,
                "HomeKit missing required fields: "
                + ", ".join(sorted(field.value for field in missing)),
            )
        elif not (_ESSENTIAL_FIELDS & self.__device.failed_fields.keys()):
            self.__set_available(True)

    async def _async_scheduled_poll(self, _now: datetime | None = None) -> None:
        await self.async_poll()

    async def async_poll(self) -> None:
        """Refresh the accessory database or read current semantic state."""
        if self.__poll_lock.locked():
            return
        async with self.__poll_lock:
            try:
                await self.__device.async_connect()
            except LOCAL_ERRORS as error:
                self.__set_available(False, str(error) or type(error).__name__)
                return

            try:
                await self.async_read(
                    tuple(self.__device.supported_fields | _ESSENTIAL_FIELDS),
                    timeout=LOCAL_READ_TIMEOUT * 3,
                )
            except LOCAL_ERRORS as error:
                self.__set_available(False, str(error) or type(error).__name__)
                return

            self.__set_available(True)

    async def async_read(
        self,
        fields: Iterable[Field],
        *,
        timeout: float | None = None,  # noqa: ASYNC109 - router read-back deadline
    ) -> dict[Field, Any]:
        """Read semantic values, updating the protocol cache on success."""
        requested = tuple(fields)
        return await self.__device.async_read(requested, timeout=timeout)

    def start_write(self, values: Mapping[Field, Any]) -> asyncio.Task[None]:
        """Start a HomeKit write that cannot be cancelled by a caller timeout."""
        return self.hass.async_create_background_task(
            self.__device.async_write(values), f"mysa {self.name} local write"
        )

    @callback
    def mark_available(self) -> None:
        """Mark healthy after a successful router read-back."""
        if not (_ESSENTIAL_FIELDS & self.__device.failed_fields.keys()) and self.ready:
            self.__set_available(True)

    @callback
    def mark_unavailable(self, error: str) -> None:
        """Mark unavailable after an uncertain write or transport failure."""
        self.__set_available(False, error)

    async def async_identify(self) -> None:
        """Ask the thermostat to identify itself over HomeKit."""
        await self.__device.async_identify()

    async def async_stop(self) -> None:
        """Stop the HA poll timer and close the protocol session."""
        if self.__poll_unsub is not None:
            self.__poll_unsub()
            self.__poll_unsub = None
        if self.__device_unsub is not None:
            self.__device_unsub()
            self.__device_unsub = None
        self.__listeners.clear()
        self.__capability_listeners.clear()
        if self.__refresh_task is not None:
            self.__refresh_task.cancel()
            await asyncio.gather(self.__refresh_task, return_exceptions=True)
            self.__refresh_task = None
        if not self.__started:
            return
        self.__started = False
        try:
            await self.__device.async_close()
        except HomeKitError as error:
            LOGGER.debug(
                "Closing local HomeKit session failed (%s)", type(error).__name__
            )

    def down_for(self) -> timedelta | None:
        """Return the outage duration while local HomeKit is unavailable."""
        if self.available or self.down_since is None:
            return None
        return dt_util.utcnow() - self.down_since

    @callback
    def diagnostics(self) -> dict[str, Any]:
        """Return semantic HomeKit diagnostics with identifiers omitted."""
        details = self.__device.diagnostics()
        return {
            "connected": self.available,
            "model": self.info.model,
            "manufacturer": self.info.manufacturer,
            "firmware": self.info.firmware,
            "fields": details["fields"],
            "last_error": self.last_error,
            "down_since": self.down_since.isoformat() if self.down_since else None,
            "recovery": details.get("recovery"),
        }
