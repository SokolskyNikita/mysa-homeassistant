"""Account-scoped MQTT telemetry lifecycle and freshness."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Mapping
from dataclasses import replace
from datetime import UTC, datetime, timedelta
import json
from types import SimpleNamespace
from typing import Any

from homeassistant.core import HomeAssistant
from mysa_client.mqtt import (
    MessageKind,
    MqttConnectionError,
    MqttMessage,
    MqttTelemetry,
)
from mysa_client.mqtt.protocol import parse_message
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mysa_local import mqtt_coordinator as mqtt_module
from custom_components.mysa_local.event_history import EventHistory
from custom_components.mysa_local.mqtt_coordinator import MysaMqttCoordinator

from .common import DEVICE_ID, hub_entry

OTHER_DEVICE_ID = "f0e1d2c3b4a5"
MODEL = "BB-V2-0-L"
pytestmark = pytest.mark.mqtt_runtime


class _FakeClock:
    def __init__(self, value: float = 100.0) -> None:
        self.value = value

    def monotonic(self) -> float:
        return self.value


class _FakeMqttClient:
    """Small async client double for the coordinator contract."""

    def __init__(
        self,
        token_provider: Any,
        devices: Mapping[str, str],
        *,
        connect_error: Exception | None = None,
        hold_connect: asyncio.Event | None = None,
    ) -> None:
        self.devices = dict(devices)
        self.connect_error = connect_error
        self.hold_connect = hold_connect
        self.connected = False
        self.connection_generation = 0
        self.dropped_events = 0
        self.connect_started = asyncio.Event()
        self.closed = asyncio.Event()
        self._events: asyncio.Queue[MqttMessage | None] = asyncio.Queue()
        self.status_requests: list[tuple[str, int]] = []
        self.request_times: list[float] = []
        self.request_clock: Callable[[], float] | None = None

    async def connect(self) -> None:
        self.connect_started.set()
        if self.hold_connect is not None:
            await self.hold_connect.wait()
        if self.connect_error is not None:
            raise self.connect_error
        self.connection_generation += 1
        self.connected = True

    async def close(self) -> None:
        self.connected = False
        self.closed.set()
        await self._events.put(None)

    async def events(self) -> AsyncIterator[MqttMessage]:
        while True:
            event = await self._events.get()
            if event is None:
                return
            yield event

    async def emit(self, message: MqttMessage) -> None:
        await self._events.put(message)

    async def request_live_status(
        self, device_id: str, *, timeout_seconds: int
    ) -> SimpleNamespace:
        self.status_requests.append((device_id, timeout_seconds))
        if self.request_clock is not None:
            self.request_times.append(self.request_clock())
        return SimpleNamespace(broker_accepted=True)


def _coordinator(hass: HomeAssistant, entry: MockConfigEntry) -> MysaMqttCoordinator:
    return MysaMqttCoordinator(
        hass,
        entry,
        token_provider=_token,
        history=EventHistory(),
    )


async def _token() -> str:
    return "test-token"


async def _until(predicate: Any, *, after: float = 1.0) -> None:
    """Wait briefly for a condition driven by another background task."""
    async with asyncio.timeout(after):
        while not predicate():  # noqa: ASYNC110 -- the observed condition is arbitrary
            await asyncio.sleep(0.001)


def _telemetry(
    device_id: str = DEVICE_ID,
    *,
    message_id: int = 1,
    humidity: float | None = 44.5,
    duty_cycle: float | None = 0.375,
    received_at: datetime | None = None,
) -> MqttMessage:
    return MqttMessage(
        device_id=device_id,
        kind=MessageKind.TELEMETRY,
        received_at=received_at or datetime.now(UTC),
        message_type=40,
        source_type=1,
        message_id=message_id,
        telemetry=MqttTelemetry(humidity=humidity, duty_cycle=duty_cycle),
    )


def _parsed_telemetry(
    *,
    device_id: str = DEVICE_ID,
    message_id: int = 1,
    humidity: float | None = 44.5,
    duty_cycle: float | None = 0.375,
    retained: bool = False,
    source_device: str | None = None,
    extra: Mapping[str, Any] | None = None,
) -> MqttMessage:
    reading: dict[str, Any] = {"hum": humidity, "dtyCycle": duty_cycle}
    body: dict[str, Any] = {"reading": reading}
    if extra:
        body.update(extra)
    payload = {
        "msg": 40,
        "src": {"type": 1, "ref": source_device or device_id},
        "id": message_id,
        "body": body,
    }
    return parse_message(
        device_id,
        json.dumps(payload, allow_nan=True).encode(),
        retained=retained,
    )


def _direct_coordinator(hass: HomeAssistant) -> MysaMqttCoordinator:
    # These synchronous acceptance tests do not start background work.
    return _coordinator(hass, hub_entry())


async def test_start_is_nonblocking_and_retries_after_connect_failure(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A broker failure does not block start and the background loop retries."""
    entry.add_to_hass(hass)
    clients: list[_FakeMqttClient] = []

    def factory(token_provider: Any, devices: Mapping[str, str]) -> _FakeMqttClient:
        client = _FakeMqttClient(
            token_provider,
            devices,
            connect_error=MqttConnectionError() if not clients else None,
        )
        clients.append(client)
        return client

    monkeypatch.setattr(mqtt_module, "MysaMqttClient", factory)
    monkeypatch.setattr(mqtt_module, "MQTT_RETRY_SECONDS", 0.01)
    monkeypatch.setattr(mqtt_module, "MQTT_MAX_RETRY_SECONDS", 0.02)
    coordinator = _coordinator(hass, entry)
    coordinator.async_set_devices({DEVICE_ID: MODEL})

    coordinator.async_start()
    await _until(lambda: len(clients) >= 1)
    await asyncio.wait_for(clients[0].connect_started.wait(), 0.2)
    await _until(lambda: len(clients) >= 2 and clients[1].connected)

    assert clients[0].closed.is_set()
    assert coordinator.connected
    await coordinator.async_stop()


async def test_unload_during_initial_offline_backoff_prevents_retry(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unloading after an initial broker failure cancels the pending retry."""
    entry.add_to_hass(hass)
    clients: list[_FakeMqttClient] = []

    def factory(token_provider: Any, devices: Mapping[str, str]) -> _FakeMqttClient:
        client = _FakeMqttClient(
            token_provider,
            devices,
            connect_error=MqttConnectionError(),
        )
        clients.append(client)
        return client

    monkeypatch.setattr(mqtt_module, "MysaMqttClient", factory)
    monkeypatch.setattr(mqtt_module, "MQTT_RETRY_SECONDS", 60.0)
    coordinator = _coordinator(hass, entry)
    coordinator.async_set_devices({DEVICE_ID: MODEL})
    coordinator.async_start()

    await _until(lambda: len(clients) == 1 and clients[0].closed.is_set())
    assert not coordinator.connected
    await coordinator.async_stop()
    await asyncio.sleep(0)

    assert len(clients) == 1
    assert coordinator._task is None


async def test_live_status_renews_after_150_seconds_and_on_new_session(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 180-second stream is renewed at 150 seconds and after reconnect."""
    clock = _FakeClock()
    monkeypatch.setattr(mqtt_module, "time", clock)
    monkeypatch.setattr(mqtt_module, "MQTT_CHECK_SECONDS", 0.001)
    coordinator = _coordinator(hass, entry)
    coordinator.async_set_devices({DEVICE_ID: MODEL})
    coordinator._changed.clear()
    client = _FakeMqttClient(_token, {DEVICE_ID: MODEL})
    client.connected = True
    client.connection_generation = 1
    task = asyncio.create_task(coordinator._maintain(client))
    try:
        await _until(lambda: len(client.status_requests) == 1)
        assert client.status_requests == [(DEVICE_ID, 180)]

        clock.value += 149.0
        await asyncio.sleep(0.01)
        assert len(client.status_requests) == 1

        clock.value += 1.1
        await _until(lambda: len(client.status_requests) == 2)

        # A new MQTT session invalidates the old per-session request schedule.
        client.connection_generation += 1
        await _until(lambda: len(client.status_requests) == 3)
        assert all(timeout == 180 for _, timeout in client.status_requests)
    finally:
        coordinator._changed.set()
        await task


async def test_valid_telemetry_triggers_quiet_refresh_before_expiry(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An accepted stream that has gone quiet is refreshed at 20 seconds."""
    clock = _FakeClock()
    monkeypatch.setattr(mqtt_module, "time", clock)
    monkeypatch.setattr(mqtt_module, "MQTT_CHECK_SECONDS", 0.001)
    coordinator = _coordinator(hass, entry)
    coordinator.async_set_devices({DEVICE_ID: MODEL})
    coordinator._changed.clear()
    client = _FakeMqttClient(_token, {DEVICE_ID: MODEL})
    client.connected = True
    client.connection_generation = 1
    client.request_clock = clock.monotonic
    coordinator._client = client
    task = asyncio.create_task(coordinator._maintain(client))
    try:
        await _until(lambda: len(client.status_requests) == 1)
        assert client.request_times == [100.0]

        # Model the device's pushed event arriving just after the request.
        clock.value += 0.1
        coordinator._accept(_telemetry(message_id=60))
        reading_expiry = coordinator._readings[(DEVICE_ID, "humidity")].expires
        clock.value += 19.8
        await asyncio.sleep(0.01)
        assert len(client.status_requests) == 1

        clock.value += 0.3
        await _until(lambda: len(client.status_requests) == 2)

        assert client.request_times[1] == pytest.approx(120.2)
        assert clock.value < reading_expiry
        assert coordinator.value(DEVICE_ID, "humidity") is not None
    finally:
        coordinator._changed.set()
        await task


async def test_no_telemetry_keeps_initial_stream_requests_at_150_seconds(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A broker-accepted request without telemetry does not prompt rapid retries."""
    clock = _FakeClock()
    monkeypatch.setattr(mqtt_module, "time", clock)
    monkeypatch.setattr(mqtt_module, "MQTT_CHECK_SECONDS", 0.001)
    coordinator = _coordinator(hass, entry)
    coordinator.async_set_devices({DEVICE_ID: MODEL})
    coordinator._changed.clear()
    client = _FakeMqttClient(_token, {DEVICE_ID: MODEL})
    client.connected = True
    client.connection_generation = 1
    client.request_clock = clock.monotonic
    task = asyncio.create_task(coordinator._maintain(client))
    try:
        await _until(lambda: len(client.status_requests) == 1)
        assert client.request_times == [100.0]

        clock.value += 149.9
        await asyncio.sleep(0.01)
        assert len(client.status_requests) == 1

        clock.value += 0.2
        await _until(lambda: len(client.status_requests) == 2)
        assert client.request_times[1] == pytest.approx(250.1)
        assert all(timeout == 180 for _, timeout in client.status_requests)
    finally:
        coordinator._changed.set()
        await task


def test_freshness_is_per_field_and_missing_values_do_not_renew(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A message missing duty cycle refreshes humidity only."""
    clock = _FakeClock()
    monkeypatch.setattr(mqtt_module, "time", clock)
    coordinator = _direct_coordinator(hass)
    coordinator.async_set_devices({DEVICE_ID: MODEL})
    coordinator._client = _FakeMqttClient(_token, {DEVICE_ID: MODEL})
    coordinator._client.connected = True

    coordinator._accept(_telemetry(message_id=10, humidity=42.25, duty_cycle=0.5))
    clock.value += 20
    coordinator._accept(_telemetry(message_id=11, humidity=43.75, duty_cycle=None))
    clock.value += 10.1

    assert coordinator.value(DEVICE_ID, "humidity") == pytest.approx(43.75)
    assert coordinator.value(DEVICE_ID, "duty_cycle") is None
    assert coordinator.has_value(DEVICE_ID, "duty_cycle")


def test_retained_wrong_device_malformed_duplicate_and_delayed_events_are_ignored(
    hass: HomeAssistant,
) -> None:
    """Only fresh, non-retained, unique telemetry for an allowed device counts."""
    coordinator = _direct_coordinator(hass)
    coordinator.async_set_devices({DEVICE_ID: MODEL})
    coordinator._client = _FakeMqttClient(_token, {DEVICE_ID: MODEL})
    coordinator._client.connected = True

    coordinator._accept(_parsed_telemetry(message_id=1, retained=True))
    coordinator._accept(_parsed_telemetry(message_id=2, source_device=OTHER_DEVICE_ID))
    coordinator._accept(parse_message(DEVICE_ID, b"{not json", retained=False))
    coordinator._accept(_parsed_telemetry(device_id=OTHER_DEVICE_ID, message_id=3))
    coordinator._accept(_parsed_telemetry(message_id=4, humidity=40, duty_cycle=0.25))
    coordinator._accept(_parsed_telemetry(message_id=4, humidity=99, duty_cycle=0.9))
    delayed = replace(
        _parsed_telemetry(message_id=5, humidity=88, duty_cycle=0.8),
        received_at=datetime.now(UTC) - timedelta(seconds=30),
    )
    coordinator._accept(delayed)

    assert coordinator.value(DEVICE_ID, "humidity") == pytest.approx(40)
    assert coordinator.value(DEVICE_ID, "duty_cycle") == pytest.approx(0.25)
    assert not coordinator.has_value(OTHER_DEVICE_ID, "humidity")


def test_fractional_readings_and_inclusive_ranges_are_preserved(
    hass: HomeAssistant,
) -> None:
    """Fractional percentages remain numeric while out-of-range values are ignored."""
    coordinator = _direct_coordinator(hass)
    coordinator.async_set_devices({DEVICE_ID: MODEL})
    coordinator._client = _FakeMqttClient(_token, {DEVICE_ID: MODEL})
    coordinator._client.connected = True

    coordinator._accept(
        _parsed_telemetry(message_id=20, humidity=43.25, duty_cycle=0.375)
    )
    assert coordinator.value(DEVICE_ID, "humidity") == pytest.approx(43.25)
    assert coordinator.value(DEVICE_ID, "duty_cycle") == pytest.approx(0.375)

    coordinator._accept(_parsed_telemetry(message_id=21, humidity=100, duty_cycle=1))
    assert coordinator.value(DEVICE_ID, "humidity") == pytest.approx(100)
    assert coordinator.value(DEVICE_ID, "duty_cycle") == pytest.approx(1)

    coordinator._accept(
        _parsed_telemetry(message_id=22, humidity=100.1, duty_cycle=-0.01)
    )
    assert coordinator.value(DEVICE_ID, "humidity") == pytest.approx(100)
    assert coordinator.value(DEVICE_ID, "duty_cycle") == pytest.approx(1)


async def test_expiry_notifies_listeners_and_restores_fallback(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When MQTT readings expire, listeners can fall back to other sources."""
    clock = _FakeClock()
    monkeypatch.setattr(mqtt_module, "time", clock)
    monkeypatch.setattr(mqtt_module, "MQTT_CHECK_SECONDS", 0.001)
    coordinator = _direct_coordinator(hass)
    coordinator.async_set_devices({DEVICE_ID: MODEL})
    client = _FakeMqttClient(_token, {DEVICE_ID: MODEL})
    client.connected = True
    coordinator._client = client
    notifications: list[None] = []
    coordinator.async_add_listener(lambda: notifications.append(None))
    coordinator._accept(_telemetry(message_id=30))
    initial_notifications = len(notifications)
    assert coordinator.value(DEVICE_ID, "humidity") is not None

    coordinator._changed.clear()
    task = asyncio.create_task(coordinator._expire(client))
    try:
        clock.value += 30.1
        await _until(lambda: coordinator.value(DEVICE_ID, "humidity") is None)
        await _until(lambda: len(notifications) > initial_notifications)
        assert coordinator.value(DEVICE_ID, "duty_cycle") is None
    finally:
        coordinator._changed.set()
        await task


async def test_device_future_timestamp_does_not_affect_local_receipt_freshness(
    hass: HomeAssistant,
) -> None:
    """Device clock fields cannot make a locally received MQTT reading stale."""
    coordinator = _direct_coordinator(hass)
    coordinator.async_set_devices({DEVICE_ID: MODEL})
    coordinator._client = _FakeMqttClient(_token, {DEVICE_ID: MODEL})
    coordinator._client.connected = True
    message = _parsed_telemetry(
        message_id=40,
        humidity=47.125,
        duty_cycle=0.625,
        extra={"time": 2_000_000_000, "Timestamp": 2_000_000_000},
    )

    coordinator._accept(message)

    assert coordinator.value(DEVICE_ID, "humidity") == pytest.approx(47.125)
    assert coordinator.value(DEVICE_ID, "duty_cycle") == pytest.approx(0.625)
    assert coordinator.diagnostics(DEVICE_ID)["readings"]["humidity"]["fresh"]


async def test_allowlist_change_recreates_client_and_discards_removed_readings(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The broker subscription set follows account devices as they change."""
    entry.add_to_hass(hass)
    clients: list[_FakeMqttClient] = []

    def factory(token_provider: Any, devices: Mapping[str, str]) -> _FakeMqttClient:
        client = _FakeMqttClient(token_provider, devices)
        clients.append(client)
        return client

    monkeypatch.setattr(mqtt_module, "MysaMqttClient", factory)
    coordinator = _coordinator(hass, entry)
    coordinator.async_set_devices({DEVICE_ID: MODEL})
    coordinator.async_start()
    await _until(lambda: len(clients) == 1 and clients[0].connected)
    await clients[0].emit(_telemetry(message_id=50))
    await _until(lambda: coordinator.value(DEVICE_ID, "humidity") is not None)

    coordinator.async_set_devices({OTHER_DEVICE_ID: MODEL})
    await _until(lambda: len(clients) == 2 and clients[1].connected)
    assert clients[0].closed.is_set()
    assert clients[1].devices == {OTHER_DEVICE_ID: MODEL}
    assert coordinator.value(DEVICE_ID, "humidity") is None
    assert not coordinator.has_value(DEVICE_ID, "humidity")

    await clients[1].emit(_telemetry(OTHER_DEVICE_ID, message_id=51))
    await _until(lambda: coordinator.value(OTHER_DEVICE_ID, "humidity") is not None)
    await coordinator.async_stop()


async def test_unload_cancels_background_tasks_and_closes_client(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stopping the integration closes the client and ends its worker task."""
    entry.add_to_hass(hass)
    clients: list[_FakeMqttClient] = []

    def factory(token_provider: Any, devices: Mapping[str, str]) -> _FakeMqttClient:
        client = _FakeMqttClient(token_provider, devices)
        clients.append(client)
        return client

    monkeypatch.setattr(mqtt_module, "MysaMqttClient", factory)
    coordinator = _coordinator(hass, entry)
    coordinator.async_set_devices({DEVICE_ID: MODEL})
    coordinator.async_start()
    await _until(lambda: len(clients) == 1 and clients[0].connected)
    task = coordinator._task
    assert task is not None
    assert not task.done()

    await coordinator.async_stop()

    assert clients[0].closed.is_set()
    assert coordinator._task is None
    assert task.done()
    assert not coordinator.connected
