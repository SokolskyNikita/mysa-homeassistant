"""Support captures must be useful, bounded, private, and passive."""

from datetime import UTC, datetime, timedelta
import json
import logging
from unittest.mock import AsyncMock, patch

from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util
from mysa_client.cloud import (
    Mode,
    MysaApiError,
    MysaConnectionError,
    MysaRateLimitError,
)
from mysa_client.cloud.const import LEGACY_API_BASE
from mysa_client.cloud.observations import Observation
from mysa_client.cloud.trace import route_template
import pytest
from pytest_homeassistant_custom_component.components.diagnostics import (
    get_diagnostics_for_config_entry,
)

from custom_components.mysa_local.event_history import (
    MAX_EVENT_BYTES,
    MAX_EVENTS,
    MAX_NOISY_EVENTS,
    RETENTION,
    EventHistory,
    error_category,
)

from .common import DEVICE_ID, HOME_ID, PAIRING_DATA, REFRESH_TOKEN, hub_entry
from .conftest import setup_entry
from .test_new_cloud_models import configure

LOGGER = "custom_components.mysa_local"


def events(history, *, event=None, command=None):
    return [
        row
        for row in history.export()["events"]
        if (event is None or row["event"] == event)
        and (command is None or row.get("command_id") == command)
    ]


def test_count_age_size_and_noise_bounds(freezer):
    history = EventHistory()
    for _ in range(MAX_EVENTS + 2):
        history.record("command", status="submitted")
    capture = history.export()
    assert len(capture["events"]) == MAX_EVENTS
    assert capture["eviction_count"] == 2
    first = capture["events"][0]
    for _ in range(1000):
        history.record("source", noisy=True, source="S", status="failure")
    assert len(events(history, event="source")) == 1
    assert history.suppressed == 999
    for index in range(MAX_NOISY_EVENTS * 2):
        history.record("source", noisy=True, attempt=index)
    assert len(events(history, event="source")) == MAX_NOISY_EVENTS
    assert first in events(history)  # noise cannot crowd out all command evidence
    for row in events(history):
        assert len(json.dumps(row).encode()) <= MAX_EVENT_BYTES
    freezer.tick(RETENTION + timedelta(seconds=1))
    assert events(history) == []
    assert history.export()["retained_from"] is None
    assert history.evicted == MAX_EVENTS + MAX_NOISY_EVENTS + 2


def test_per_event_bound_and_export_is_detached():
    history = EventHistory()
    now = datetime.now(UTC)
    baseline = {
        key: Observation(21, "S", now, now)
        for key in ("mode", "heat_setpoint", "cool_setpoint")
    }
    with patch("custom_components.mysa_local.event_history.MAX_EVENT_BYTES", 512):
        history.record("command", baseline=baseline, observations=baseline)
        row = events(history)[0]
        assert row["truncated"]
        assert len(json.dumps(row).encode()) <= 512
    row["secret"] = "never stored"
    assert "secret" not in events(history)[0]


def test_secrets_never_enter_buffer_logs_or_export(caplog):
    caplog.set_level(logging.DEBUG, logger=LOGGER)
    history = EventHistory()
    secrets = [
        REFRESH_TOKEN,
        HOME_ID,
        DEVICE_ID,
        "192.0.2.10",
        "SERIAL0001",
        "Living room",
        "user@example.com",
        *PAIRING_DATA.values(),
    ]
    payload = {
        str(secret): {"credentials": secret, "url": f"https://host/{secret}"}
        for secret in secrets
    }
    command = history.command_id()
    with history.command(command, DEVICE_ID):
        history.record(
            "command",
            status="failed",
            requested={**payload, "heat_setpoint": 23, "mode": payload},
            baseline={
                "heat_setpoint": {
                    "value": payload,
                    "observed_at": "secret-invalid-date",
                    **payload,
                }
            },
            detail=MysaConnectionError(str(payload)),
            error=error_category(MysaConnectionError(str(payload))),
            route=f"/state/{DEVICE_ID}/update?token={REFRESH_TOKEN}",
            unknown=payload,
        )
    # A second pass also rejects accidentally introduced fields/values.
    history._events[0][2]["unexpected"] = payload
    exported = history.export()
    assert exported["events"][0]["requested"]["heat_setpoint"] == 23
    assert exported["events"][0]["command_id"] == command
    assert exported["events"][0]["device"] == history.label(DEVICE_ID)
    assert history.label(DEVICE_ID) != history.label("another-device")
    log = " ".join(r.getMessage() for r in caplog.records if r.name.startswith(LOGGER))
    # Ignore the intentionally injected corruption when checking pre-export storage.
    history._events[0][2].pop("unexpected")
    for blob in (json.dumps(exported), log, json.dumps(history._events[0][2])):
        for secret in secrets:
            if isinstance(secret, str) and secret not in {"IP"}:
                assert secret not in blob
    assert route_template(f"/unknown/{DEVICE_ID}") == "unknown"
    assert history.dropped == 0


def test_capture_failure_is_passive():
    history = EventHistory()
    with patch.object(history, "_sanitize", side_effect=RuntimeError("secret")):
        history.record("command", status="submitted")
    assert history.dropped == 1
    history.record("command", status="accepted")
    assert len(events(history)) == 1
    history.close()
    history.record("command", device_id="ignored")
    assert not events(history)
    assert not history._devices
    assert not history._dedup


async def test_v3_download_reconstructs_supersession_and_delayed_report(
    hass, hass_client, cloud, caplog
):
    entry = hub_entry(subentries=[configure(cloud, "BB-V3-0")])
    await setup_entry(hass, entry)
    coordinator = entry.runtime_data.cloud
    history = entry.runtime_data.history
    original = coordinator.state(DEVICE_ID).heat_setpoint
    cloud.apply_updates = False
    caplog.set_level(logging.DEBUG, logger=LOGGER)
    with patch("custom_components.mysa_local.cloud_coordinator.CLOUD_WRITE_SPACING", 0):
        await coordinator.async_set_temperature(DEVICE_ID, Mode.HEAT, 23)
        older = coordinator.pending[DEVICE_ID]["heat_setpoint"].correlation_id
        await coordinator.async_set_temperature(DEVICE_ID, Mode.HEAT, 24)
    newer = coordinator.pending[DEVICE_ID]["heat_setpoint"].correlation_id
    assert older != newer
    assert [r["status"] for r in events(history, event="command", command=older)] == [
        "submitted",
        "accepted",
        "superseded",
    ]
    assert coordinator.state(DEVICE_ID).heat_setpoint == original
    for temperature in (23, 24):
        cloud.state["targetHeat"]["reported"].update(
            setpoint=temperature, timestamp=dt_util.utcnow().timestamp()
        )
        await coordinator.async_refresh()
    assert len(cloud.updates) == 2
    # Download before reload, through HA's actual diagnostics HTTP endpoint.
    diag = await get_diagnostics_for_config_entry(hass, hass_client, entry)
    rows = diag["event_history"]["events"]
    commands = [r for r in rows if r["event"] == "command" and r["command_id"] == newer]
    assert [r["status"] for r in commands] == ["submitted", "accepted", "confirmed"]
    assert commands[0]["requested"] == {"heat_setpoint": 24}
    assert commands[0]["baseline"]["heat_setpoint"]["value"] == original
    assert commands[-1]["evidence"] == "device_report"
    assert commands[-1]["observations"]["heat_setpoint"]["value"] == 24
    assert commands[-1]["observations"]["heat_setpoint"]["observed_at"] != "redacted"
    assert any(
        r.get("reason") == "value_mismatch"
        and r["observations"]["heat_setpoint"]["value"] == 23
        for r in rows
    )
    requests = [r for r in rows if r["event"] == "request" and r["command_id"] == newer]
    assert requests[0]["http_status"] == 200
    assert requests[0]["route"] == "/state/{id}/update"
    assert requests[0]["duration_ms"] >= 0
    assert diag["thermostats"][0]["device"] == commands[0]["device"]
    assert newer in caplog.text
    assert history.dropped == 0


@pytest.mark.usefixtures("controller")
@pytest.mark.parametrize(
    ("error", "status"),
    [
        (MysaConnectionError("secret"), "ambiguous"),
        (MysaApiError("secret", 400), "failed"),
        (MysaRateLimitError("secret", 60), "failed"),
    ],
)
async def test_failed_requests_have_safe_lifecycle(hass, cloud, caplog, error, status):
    entry = hub_entry()
    await setup_entry(hass, entry)
    coordinator = entry.runtime_data.cloud
    caplog.set_level(logging.DEBUG, logger=LOGGER)
    with (
        patch.object(coordinator.client, "update_state", AsyncMock(side_effect=error)),
        pytest.raises(HomeAssistantError),
    ):
        await coordinator.async_set_temperature(DEVICE_ID, Mode.HEAT, 24)
    rows = events(entry.runtime_data.history, event="command")
    assert [r["status"] for r in rows] == ["submitted", status]
    assert rows[-1]["error"] == error_category(error)
    assert "secret" not in json.dumps(rows)
    assert "secret" not in " ".join(
        r.getMessage() for r in caplog.records if r.name.startswith(LOGGER)
    )


@pytest.mark.usefixtures("controller")
async def test_storage_unchanged_expiry_and_reload(hass, cloud):
    entry = hub_entry()
    await setup_entry(hass, entry)
    coordinator = entry.runtime_data.cloud
    history = entry.runtime_data.history
    with patch("custom_components.mysa_local.cloud_coordinator.CLOUD_WRITE_SPACING", 0):
        await coordinator.async_set_temperature(
            DEVICE_ID, Mode.HEAT, coordinator.state(DEVICE_ID).heat_setpoint
        )
        assert events(history, event="command")[-1]["status"] == "unchanged"
        await coordinator.async_set_display_unit(DEVICE_ID, "F")
        await coordinator.async_refresh()
        assert events(history, event="command")[-1]["status"] == "stored"
        assert (
            events(history, event="command")[-1]["evidence"] == "configuration_storage"
        )
        cloud.apply_updates = False
        await coordinator.async_set_temperature(DEVICE_ID, Mode.HEAT, 24)
    command = coordinator.pending[DEVICE_ID]["heat_setpoint"]
    coordinator._reconcile_pending(
        coordinator.data, command.expires_at + timedelta(seconds=1)
    )
    assert (
        events(history, event="command", command=command.correlation_id)[-1]["status"]
        == "expired"
    )
    assert await hass.config_entries.async_reload(entry.entry_id)
    assert history.closed
    assert not events(history)
    assert entry.runtime_data.history is not history
    assert not events(entry.runtime_data.history, event="command")


@pytest.mark.usefixtures("controller")
async def test_local_write_and_cloud_fallback_share_capture(hass, cloud):
    entry = hub_entry()
    await setup_entry(hass, entry)
    thermostat = next(iter(entry.runtime_data.thermostats.values()))
    await thermostat.async_set_temperature(23)
    rows = events(entry.runtime_data.history, event="command")
    assert [r["status"] for r in rows] == ["submitted", "accepted"]
    assert all(r["transport"] == "homekit" for r in rows)
    assert len({r["command_id"] for r in rows}) == 1
    assert cloud.updates == []
    thermostat.local.mark_unavailable("secret")
    await thermostat.async_set_temperature(24)
    cloud_rows = [
        r for r in events(entry.runtime_data.history) if r.get("transport") == "cloud"
    ]
    assert any(r.get("status") == "fallback" for r in cloud_rows)
    assert len(cloud.updates) == 1


@pytest.mark.usefixtures("controller")
async def test_source_failure_backoff_and_recovery_do_not_add_io(hass, cloud):
    entry = hub_entry()
    await setup_entry(hass, entry)
    coordinator = entry.runtime_data.cloud
    with patch.object(
        coordinator.client,
        "get_states",
        AsyncMock(side_effect=MysaConnectionError("secret")),
    ) as read:
        await coordinator.async_refresh()
        await coordinator.async_refresh()
    assert read.await_count == 2
    assert (
        len(
            [
                r
                for r in events(entry.runtime_data.history, event="source")
                if r.get("source") == "S"
            ]
        )
        == 1
    )
    await coordinator.async_refresh()
    assert any(
        r.get("status") == "recovery"
        for r in events(entry.runtime_data.history, event="source")
    )
    assert cloud.updates == []


@pytest.mark.usefixtures("controller")
@pytest.mark.parametrize("status", [400, 404, 429, 503])
async def test_http_failure_metadata_and_request_privacy(hass, cloud, caplog, status):
    entry = hub_entry()
    await setup_entry(hass, entry)
    coordinator = entry.runtime_data.cloud
    cloud.path_status[f"/state/{DEVICE_ID}/update"] = status
    caplog.set_level(logging.DEBUG, logger=LOGGER)
    with pytest.raises(HomeAssistantError):
        await coordinator.async_set_temperature(DEVICE_ID, Mode.HEAT, 24)
    request = events(entry.runtime_data.history, event="request")[-1]
    assert request["http_status"] == status
    assert request["route"] == "/state/{id}/update"
    assert request["host"] == "modern"
    assert request["duration_ms"] >= 0
    assert request["error"] != "redacted"
    assert len(cloud.updates) == 1  # no retry of uncertain writes
    blob = json.dumps(request)
    assert DEVICE_ID not in blob
    assert "authorization" not in blob
    assert "body" not in blob


@pytest.mark.usefixtures("controller")
async def test_protocol_fallback_and_auth_retry_keep_command_id(hass, cloud):
    entry = hub_entry()
    await setup_entry(hass, entry)
    coordinator = entry.runtime_data.cloud
    history = entry.runtime_data.history
    cloud.backend_status = 403
    cloud.mock.post(f"{LEGACY_API_BASE}/devices/{DEVICE_ID}/", json={})
    await coordinator.async_set_display_unit(DEVICE_ID, "F")
    command = coordinator.pending[DEVICE_ID]["display_unit"].correlation_id
    rows = events(history, command=command)
    assert any(r["event"] == "fallback" and r["host"] == "legacy" for r in rows)
    requests = [r for r in rows if r["event"] == "request"]
    assert [(r["host"], r["http_status"]) for r in requests] == [
        ("modern", 403),
        ("legacy", 200),
    ]
    assert all(r["route"] == "/devices/{id}" for r in requests)
    cloud.backend_status = None
    cloud.path_status[f"/state/{DEVICE_ID}/update"] = 401
    with (
        patch("custom_components.mysa_local.cloud_coordinator.CLOUD_WRITE_SPACING", 0),
        pytest.raises(HomeAssistantError),
    ):
        await coordinator.async_set_temperature(DEVICE_ID, Mode.HEAT, 24)
    command = coordinator.pending[DEVICE_ID]["heat_setpoint"].correlation_id
    assert any(
        r["event"] == "retry" and r.get("reason") == "auth_refresh"
        for r in events(history, command=command)
    )
    assert len(events(history, event="request", command=command)) == 2


@pytest.mark.usefixtures("controller")
async def test_download_scrubs_nested_payloads_and_preserves_two_devices(
    hass, hass_client, cloud, caplog
):
    entry = hub_entry()
    await setup_entry(hass, entry)
    runtime = entry.runtime_data
    secrets = [
        "capture-secret-token",
        "pairing-secret",
        "serial-secret",
        "named-device",
        "2001:db8::1",
    ]
    payload = {s: {"tokens": [s], "url": f"https://host/{s}"} for s in secrets}
    for raw in (
        runtime.cloud.state(DEVICE_ID).raw,
        runtime.cloud.record(DEVICE_ID).raw,
        runtime.cloud.capabilities(DEVICE_ID).raw,
    ):
        raw.update(payload)
        raw["unknown"] = {"Name": "named-device", "credentials": payload}
    caplog.set_level(logging.DEBUG, logger=LOGGER)
    ids = [DEVICE_ID, "second-private-device"]
    runtime.cloud.source_health["S"]["partial_errors"] = dict.fromkeys(ids, "failure")
    for device in ids:
        runtime.history.record(
            "command",
            device_id=device,
            command_id=runtime.history.command_id(),
            operation="set_state",
            status="failed",
            error=error_category(MysaConnectionError(str(payload))),
            requested={"heat_setpoint": 24, "unknown": payload},
        )
    diag = await get_diagnostics_for_config_entry(hass, hass_client, entry)
    rows = [r for r in diag["event_history"]["events"] if r["event"] == "command"]
    assert len({r["device"] for r in rows}) == 2
    labels = diag["thermostats"][0]["cloud"]["sources"]["S"]["partial_errors"]
    assert set(labels) == {r["device"] for r in rows}
    assert diag["thermostats"][0]["device"] == rows[0]["device"]
    log = " ".join(r.getMessage() for r in caplog.records if r.name.startswith(LOGGER))
    for secret in [*secrets, *ids]:
        assert secret not in json.dumps(diag)
        assert secret not in log


@pytest.mark.usefixtures("controller")
async def test_capture_failure_and_cancellation_do_not_change_commands(hass, cloud):
    import asyncio

    entry = hub_entry()
    await setup_entry(hass, entry)
    coordinator = entry.runtime_data.cloud
    history = entry.runtime_data.history
    with patch.object(history, "_sanitize", side_effect=RuntimeError("secret")):
        await coordinator.async_set_temperature(DEVICE_ID, Mode.HEAT, 24)
    assert len(cloud.updates) == 1
    assert history.dropped > 0
    started = asyncio.Event()

    async def waiting(*_args):
        started.set()
        await asyncio.Future()

    with (
        patch.object(coordinator.client, "update_state", side_effect=waiting),
        patch("custom_components.mysa_local.cloud_coordinator.CLOUD_WRITE_SPACING", 0),
    ):
        task = asyncio.create_task(
            coordinator.async_set_temperature(DEVICE_ID, Mode.HEAT, 25)
        )
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert events(history, event="command")[-1]["status"] == "ambiguous"
    assert events(history, event="command")[-1]["error"] == "cancelled"
    assert len(cloud.updates) == 1
