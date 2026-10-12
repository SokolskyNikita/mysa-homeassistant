"""Account MQTT telemetry, with renewable streams and per-field freshness."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from contextlib import suppress
import time
from typing import Any

from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.util import dt as dt_util
from mysa_client.mqtt import MqttError, MqttMessage, MysaMqttClient
from mysa_client.runtime.telemetry import (
    MqttLiveStatusRenewal,
    MqttTelemetryState,
    TelemetryReading,
)

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
        self._telemetry = MqttTelemetryState(
            max_age=MQTT_MAX_AGE,
            monotonic=self._monotonic,
            utcnow=dt_util.utcnow,
        )
        self._listeners: set[CALLBACK_TYPE] = set()
        self._client: MysaMqttClient | None = None
        self._task: asyncio.Task[None] | None = None
        self._changed = asyncio.Event()
        self._stopped = False

    @staticmethod
    def _monotonic() -> float:
        """Read the monotonic clock through the adapter's injectable module."""
        return time.monotonic()

    @property
    def connected(self) -> bool:
        """Whether the broker has accepted this account's subscriptions."""
        return bool(not self._stopped and self._client and self._client.connected)

    @property
    def _readings(self) -> Mapping[tuple[str, str], TelemetryReading]:
        """Compatibility view for diagnostics and existing integration tests."""
        return self._telemetry.readings

    @callback
    def async_add_listener(self, listener: CALLBACK_TYPE) -> CALLBACK_TYPE:
        """Subscribe to new readings, expiry, and connection changes."""
        self._listeners.add(listener)
        return lambda: self._listeners.discard(listener)

    @callback
    def async_set_devices(self, devices: Mapping[str, str]) -> None:
        """Replace the allowlist after account discovery or account removal."""
        if not self._telemetry.set_devices(devices):
            return
        self._devices = dict(devices)
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
        self._telemetry.clear()
        self._notify()

    def value(self, device_id: str, field: str) -> float | None:
        """A fresh live value, independent of HTTPS success and device clocks."""
        return self._telemetry.value(device_id, field, connected=self.connected)

    def has_value(self, device_id: str, field: str) -> bool:
        """Whether a valid reading has established that this field exists."""
        return self._telemetry.has_value(device_id, field)

    def diagnostics(self, device_id: str) -> dict[str, Any]:
        """Describe freshness without credentials, topic names, or raw packets."""
        return self._telemetry.diagnostics(
            device_id,
            connected=self.connected,
            dropped_events=self._client.dropped_events if self._client else 0,
        )

    @callback
    def _notify(self) -> None:
        self._telemetry.note_visibility(connected=self.connected)
        for listener in list(self._listeners):
            listener()

    @callback
    def _accept(self, message: MqttMessage) -> None:
        """Delegate telemetry filtering and freshness to the reusable client runtime."""
        if self._telemetry.accept(message):
            self._notify()

    async def _consume(self, client: MysaMqttClient) -> None:
        async for message in client.events():
            self._accept(message)

    async def _maintain(self, client: MysaMqttClient) -> None:
        renewal = MqttLiveStatusRenewal(
            quiet_refresh_seconds=MQTT_QUIET_REFRESH_SECONDS,
            renew_seconds=MQTT_RENEW_SECONDS,
            retry_seconds=MQTT_RETRY_SECONDS,
            stream_seconds=MQTT_STREAM_SECONDS,
        )
        while not self._changed.is_set():
            if client.connected:
                renewal.begin_session(client.connection_generation)
                for device_id in tuple(self._devices):
                    now = time.monotonic()
                    readings = [
                        reading
                        for (dev, _field), reading in self._readings.items()
                        if dev == device_id
                    ]
                    if not renewal.should_request(
                        device_id, now=now, readings=readings
                    ):
                        continue
                    renewal.request_started(device_id, now=now)
                    try:
                        result = await client.request_live_status(
                            device_id, timeout_seconds=renewal.stream_seconds
                        )
                        accepted = result.broker_accepted is True
                    except MqttError:
                        accepted = False
                    renewal.request_finished(
                        device_id, accepted=accepted, now=time.monotonic()
                    )
                    if self._changed.is_set():
                        return
            with suppress(TimeoutError):
                await asyncio.wait_for(self._changed.wait(), MQTT_CHECK_SECONDS)

    async def _expire(self, client: MysaMqttClient) -> None:
        # Keep expiry notifications independent of requests to slow devices.
        while not self._changed.is_set():
            if self._telemetry.visibility_changed(connected=client.connected):
                self._notify()
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
                self._telemetry.clear()
                self._notify()
            if not self._changed.is_set():
                with suppress(TimeoutError):
                    await asyncio.wait_for(self._changed.wait(), retry)
                retry = min(retry * 2, MQTT_MAX_RETRY_SECONDS)
