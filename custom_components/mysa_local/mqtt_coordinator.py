"""Account MQTT telemetry, with renewable streams and per-field freshness."""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Awaitable, Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime
import math
import time
from typing import Any

from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.util import dt as dt_util
from mysa_client.mqtt import MessageKind, MqttError, MqttMessage, MysaMqttClient

from .const import LOGGER
from .data import MysaConfigEntry
from .event_history import EventHistory

MQTT_MAX_AGE = 30.0
MQTT_QUIET_REFRESH_SECONDS = 20.0
MQTT_RENEW_SECONDS = 150.0
MQTT_STREAM_SECONDS = 180
MQTT_CHECK_SECONDS = 5.0
MQTT_RETRY_SECONDS = 60.0
MQTT_MAX_RETRY_SECONDS = 300.0


@dataclass(frozen=True, slots=True)
class _Reading:
    value: float
    received_at: datetime
    expires: float


class MysaMqttCoordinator:
    """Maintain one read-only telemetry session for configured account devices.

    MQTT is supplementary: authentication, broker or stream failures never
    prevent the HomeKit and HTTPS paths from starting or continuing to work.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        entry: MysaConfigEntry,
        token_provider: Callable[[], Awaitable[str]],
        *,
        history: EventHistory,
    ) -> None:
        """Prepare a session; account discovery supplies the device allowlist."""
        self.hass = hass
        self.entry = entry
        self.history = history
        self._token_provider = token_provider
        self._devices: dict[str, str] = {}
        self._readings: dict[tuple[str, str], _Reading] = {}
        self._seen_fields: set[tuple[str, str]] = set()
        self._message_ids: dict[str, deque[int]] = {}
        self._listeners: set[CALLBACK_TYPE] = set()
        self._visible: frozenset[tuple[str, str]] = frozenset()
        self._client: MysaMqttClient | None = None
        self._task: asyncio.Task[None] | None = None
        self._changed = asyncio.Event()
        self._stopped = False

    @property
    def connected(self) -> bool:
        """Whether the broker has accepted this account's subscriptions."""
        return bool(not self._stopped and self._client and self._client.connected)

    @callback
    def async_add_listener(self, listener: CALLBACK_TYPE) -> CALLBACK_TYPE:
        """Subscribe to new readings, expiry, and connection changes."""
        self._listeners.add(listener)
        return lambda: self._listeners.discard(listener)

    @callback
    def async_set_devices(self, devices: Mapping[str, str]) -> None:
        """Replace the allowlist after account discovery or account removal."""
        if self._devices == devices:
            return
        self._devices = dict(devices)
        for key in list(self._readings):
            if key[0] not in devices:
                self._readings.pop(key)
        self._seen_fields = {key for key in self._seen_fields if key[0] in devices}
        self._message_ids = {
            dev: ids for dev, ids in self._message_ids.items() if dev in devices
        }
        self._changed.set()
        self._notify()

    @callback
    def async_start(self) -> None:
        """Connect in the background without holding up integration setup."""
        if self._task is None:
            self._stopped = False
            self._task = self.entry.async_create_background_task(
                self.hass, self._run(), "mysa MQTT telemetry"
            )

    async def async_stop(self) -> None:
        """Stop renewal, event consumption, reconnection, and the network worker."""
        self._stopped = True
        self._changed.set()
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        self._readings.clear()
        self._notify()

    def value(self, device_id: str, field: str) -> float | None:
        """A fresh live value, independent of HTTPS success and device clocks."""
        reading = self._readings.get((device_id, field))
        if (
            self.connected
            and device_id in self._devices
            and reading is not None
            and time.monotonic() < reading.expires
        ):
            return reading.value
        return None

    def has_value(self, device_id: str, field: str) -> bool:
        """Whether a valid reading has established that this field exists."""
        return (device_id, field) in self._seen_fields

    def diagnostics(self, device_id: str) -> dict[str, Any]:
        """Describe freshness without credentials, topic names, or raw packets."""
        return {
            "connected": self.connected,
            "subscribed": device_id in self._devices,
            "dropped_events": self._client.dropped_events if self._client else 0,
            "readings": {
                field: {
                    "value": reading.value,
                    "received_at": reading.received_at.isoformat(),
                    "fresh": self.value(dev, field) is not None,
                }
                for (dev, field), reading in self._readings.items()
                if dev == device_id
            },
        }

    @callback
    def _notify(self) -> None:
        self._visible = frozenset(
            key for key in self._readings if self.value(*key) is not None
        )
        for listener in list(self._listeners):
            listener()

    @callback
    def _accept(self, message: MqttMessage) -> None:
        """Accept only live owned-device telemetry, aging each field separately."""
        if (
            message.device_id not in self._devices
            or message.retained
            or message.kind != MessageKind.TELEMETRY
            or message.telemetry is None
        ):
            return
        # received_at is stamped by our library, not the thermostat. Account
        # snapshots and device timestamps cannot extend these live readings.
        age = (dt_util.utcnow() - message.received_at).total_seconds()
        if age < 0 or age >= MQTT_MAX_AGE:
            return
        ids = self._message_ids.setdefault(message.device_id, deque(maxlen=128))
        if message.message_id is not None:
            if message.message_id in ids:
                return
            ids.append(message.message_id)
        changed = False
        for field, upper in (("humidity", 100.0), ("duty_cycle", 1.0)):
            value = getattr(message.telemetry, field)
            if value is None or not math.isfinite(value) or not 0 <= value <= upper:
                continue
            key = (message.device_id, field)
            self._readings[key] = _Reading(
                value, message.received_at, time.monotonic() + MQTT_MAX_AGE - age
            )
            self._seen_fields.add(key)
            changed = True
        if changed:
            self._notify()

    async def _consume(self, client: MysaMqttClient) -> None:
        async for message in client.events():
            self._accept(message)

    async def _maintain(self, client: MysaMqttClient) -> None:
        due: dict[str, float] = {}
        requested: dict[str, float] = {}
        generation = -1
        while not self._changed.is_set():
            if client.connected:
                if generation != client.connection_generation:
                    generation = client.connection_generation
                    due.clear()
                    requested.clear()
                for device_id in tuple(self._devices):
                    now = time.monotonic()
                    last_request = requested.get(device_id, 0)
                    readings = [
                        reading.expires - MQTT_MAX_AGE
                        for (dev, _field), reading in self._readings.items()
                        if dev == device_id
                    ]
                    # Some devices go quiet despite the advertised 180-second
                    # stream. Refresh before expiry, but only if the previous
                    # request produced a reading. Silent devices retain the
                    # slower renewal/backoff instead of being polled rapidly.
                    quiet = bool(
                        readings
                        and max(readings) >= last_request
                        and now - min(readings) >= MQTT_QUIET_REFRESH_SECONDS
                        and now - last_request >= MQTT_QUIET_REFRESH_SECONDS
                    )
                    if now < due.get(device_id, 0) and not quiet:
                        continue
                    requested[device_id] = now
                    try:
                        result = await client.request_live_status(
                            device_id, timeout_seconds=MQTT_STREAM_SECONDS
                        )
                        accepted = result.broker_accepted is True
                    except MqttError:
                        accepted = False
                    due[device_id] = time.monotonic() + (
                        MQTT_RENEW_SECONDS if accepted else MQTT_RETRY_SECONDS
                    )
                    if self._changed.is_set():
                        return
            with suppress(TimeoutError):
                await asyncio.wait_for(self._changed.wait(), MQTT_CHECK_SECONDS)

    async def _expire(self, client: MysaMqttClient) -> None:
        # Keep expiry notifications independent of requests to slow devices.
        was_connected = False
        while not self._changed.is_set():
            visible = frozenset(
                key for key in self._readings if self.value(*key) is not None
            )
            if visible != self._visible or was_connected != client.connected:
                self._notify()
            was_connected = client.connected
            with suppress(TimeoutError):
                await asyncio.wait_for(self._changed.wait(), MQTT_CHECK_SECONDS)

    async def _run(self) -> None:
        retry = MQTT_RETRY_SECONDS
        while not self._stopped:
            if not self._devices:
                self._changed.clear()
                await self._changed.wait()
                continue
            self._changed.clear()
            client = MysaMqttClient(self._token_provider, self._devices)
            self._client = client
            tasks: list[asyncio.Task[None]] = []
            try:
                await client.connect()
                retry = MQTT_RETRY_SECONDS
                self.history.record(
                    "connection", noisy=True, transport="mqtt", status="connected"
                )
                tasks = [
                    asyncio.create_task(self._consume(client)),
                    asyncio.create_task(self._maintain(client)),
                    asyncio.create_task(self._expire(client)),
                ]
                done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    task.result()
            except MqttError as err:
                self.history.record(
                    "connection", noisy=True, transport="mqtt", status="failure"
                )
                LOGGER.debug("MQTT telemetry is unavailable (%s)", type(err).__name__)
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                await client.close()
                self._client = None
                self._readings.clear()
                self._notify()
            if not self._changed.is_set():
                with suppress(TimeoutError):
                    await asyncio.wait_for(self._changed.wait(), retry)
                retry = min(retry * 2, MQTT_MAX_RETRY_SECONDS)
