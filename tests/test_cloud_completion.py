"""Cloud observations, confirmation, routing and native schedule regressions."""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

from mysa_client.cloud import (
    DeviceRecord,
    DeviceState,
    Mode,
    MysaApiError,
    MysaConnectionError,
    MysaNotFoundError,
    MysaRateLimitError,
)
from mysa_client.cloud.schedules import WEEKDAYS
import pytest

from custom_components.mysa_local.cloud_state import pending_command, reconcile
from custom_components.mysa_local.schedule import (
    async_clear_schedule,
    async_set_schedule,
)
from tests.common import DEVICE_ID, hub_entry
from tests.conftest import setup_entry

NOW = datetime(2026, 10, 12, 12, tzinfo=UTC)


def structured(now=NOW, **sections):
    return DeviceState.from_api(
        DEVICE_ID,
        {
            "latestTelemetry": {
                "isConnected": True,
                "lastConnected": now.timestamp(),
                "reading": {
                    "timestamp": now.timestamp(),
                    "roomTemperature": 21,
                    "humidity": 40,
                },
            },
            **sections,
        },
    )


def legacy(now=NOW, **fields):
    return DeviceState.from_legacy(
        DEVICE_ID,
        {
            "CorrectedTemp": {"v": 22, "t": now.timestamp()},
            **fields,
        },
    )


def test_per_field_selection_retains_age_and_rejects_future(freezer):
    freezer.move_to(NOW)
    old = reconcile(
        DEVICE_ID,
        [
            structured(),
            legacy(
                NOW + timedelta(seconds=1),
                Humidity={"v": 50, "t": (NOW - timedelta(minutes=3)).timestamp()},
            ),
        ],
        None,
        None,
        NOW,
    )
    assert old.room_temperature == 22
    assert old.humidity == 40
    assert old.observations["room_temperature"].source == "R"
    future = legacy(NOW + timedelta(days=1), Humidity={"v": 99, "t": "bad"})
    kept = reconcile(DEVICE_ID, [future], None, old, NOW + timedelta(minutes=2))
    assert kept.room_temperature == 22
    assert kept.observations["room_temperature"] == old.observations["room_temperature"]
    assert kept.humidity == 40
    missing = reconcile(DEVICE_ID, [], None, kept, NOW + timedelta(hours=1))
    assert missing.room_temperature == 22
    assert not missing.observations["room_temperature"].fresh(NOW + timedelta(hours=1))


def test_unknown_measurement_clock_cannot_replace_known_value(freezer):
    freezer.move_to(NOW)
    old = structured()
    unknown = DeviceState.from_legacy(DEVICE_ID, {"CorrectedTemp": {"v": 99}})
    state = reconcile(DEVICE_ID, [unknown], None, old, NOW)
    assert state.room_temperature == 21


def test_record_units_are_stored_and_reported_units_remain_available(freezer):
    freezer.move_to(NOW)
    state = structured(
        physicalInterface={"reported": {"timestamp": NOW.timestamp(), "format": "C"}}
    )
    record = DeviceRecord.from_api(DEVICE_ID, {"Format": "fahrenheit"})
    selected = reconcile(DEVICE_ID, [state], record, None, NOW)
    assert selected.display_unit == "F"
    assert selected.observations["display_unit"].kind == "stored"
    assert selected.reported_observations["display_unit"].value == "C"


def test_pending_confirmation_needs_changed_report_and_expires(freezer):
    freezer.move_to(NOW)
    old = structured(
        targetHeat={"reported": {"setpoint": 20, "timestamp": NOW.timestamp()}}
    )
    command = pending_command({"heat_setpoint": 23}, old, NOW + timedelta(seconds=1))
    command.reconcile(old, None, NOW + timedelta(seconds=5))
    assert command.status == "pending"
    freezer.tick(timedelta(seconds=10))
    current = structured(
        NOW + timedelta(seconds=10),
        targetHeat={
            "reported": {
                "setpoint": 23,
                "timestamp": (NOW + timedelta(seconds=10)).timestamp(),
            }
        },
    )
    command.reconcile(current, None, NOW + timedelta(seconds=10))
    assert command.status == "confirmed"
    assert command.confirmation == {"heat_setpoint": "S"}
    expired = pending_command({"heat_setpoint": 24}, current, NOW)
    expired.reconcile(current, None, NOW + timedelta(minutes=3))
    assert expired.status == "expired"
    assert pending_command({"heat_setpoint": 23}, current, NOW).status == "unchanged"


@pytest.mark.usefixtures("controller")
async def test_independent_sources_and_pending_state(hass, cloud):
    entry = hub_entry()
    await setup_entry(hass, entry)
    coordinator = entry.runtime_data.cloud
    old = coordinator.state(DEVICE_ID)
    cloud.apply_updates = False
    await coordinator.async_set_temperature(DEVICE_ID, Mode.HEAT, 24)
    assert coordinator.state(DEVICE_ID).heat_setpoint == old.heat_setpoint
    command = coordinator.pending[DEVICE_ID]["heat_setpoint"]
    assert command.status == "pending"
    now = datetime.now(UTC)
    good = legacy(now)
    with (
        patch.object(
            coordinator.client,
            "get_states",
            AsyncMock(side_effect=MysaConnectionError("S down")),
        ),
        patch.object(
            coordinator.client,
            "get_legacy_states",
            AsyncMock(return_value={DEVICE_ID: good}),
        ),
    ):
        await coordinator.async_refresh()
    assert coordinator.last_update_success
    assert coordinator.state(DEVICE_ID).room_temperature == 22
    assert coordinator.source_health["S"]["success"] is False
    assert coordinator.source_health["legacy"]["success"] is True
    assert command.status == "pending"
    assert coordinator.state(DEVICE_ID).heat_setpoint == old.heat_setpoint


@pytest.mark.usefixtures("controller")
async def test_native_clear_uses_journal_and_preserves_other_device(hass, cloud):
    entry = hub_entry()
    await setup_entry(hass, entry)
    thermostat = next(iter(entry.runtime_data.thermostats.values()))
    client = thermostat.cloud.client
    actions = {day: [{"Minute": 10, "SetPoint": 22}] for day in WEEKDAYS}
    records = [
        {"Device": DEVICE_ID, "ScheduledActions": deepcopy(actions)},
        {"Device": "unrelated", "ScheduledActions": deepcopy(actions)},
    ]

    async def native(operation, method, path, **kwargs):
        if operation == "schedules_v2":
            raise MysaNotFoundError("no schedule")
        if method == "GET":
            return {"Schedules": deepcopy(records)}
        assert path.endswith(DEVICE_ID)
        assert kwargs["body"]["Model"] == "BB-V2-0-L"
        records[0]["ScheduledActions"] = kwargs["body"]["ScheduledActions"]
        return None

    with (
        patch.object(client, "_legacy_request", native),
        patch.object(
            client,
            "get_devices",
            AsyncMock(return_value={DEVICE_ID: thermostat.cloud_record}),
        ),
    ):
        await async_clear_schedule(thermostat)
    assert all(not events for events in records[0]["ScheduledActions"].values())
    assert records[1]["ScheduledActions"] == actions


@pytest.mark.usefixtures("controller")
async def test_malformed_discovery_stops_replacement_before_mutation(hass, cloud):
    from datetime import time

    entry = hub_entry()
    await setup_entry(hass, entry)
    thermostat = next(iter(entry.runtime_data.thermostats.values()))
    with (
        patch.object(
            thermostat.cloud.client,
            "get_all_schedules",
            AsyncMock(side_effect=MysaApiError("bad schedule")),
        ),
        patch.object(thermostat.cloud.client, "remove_schedule", AsyncMock()) as remove,
        pytest.raises(MysaApiError),
    ):
        await async_set_schedule(
            thermostat, [{"days": ["mon"], "time": time(8), "temperature": 22}]
        )
    remove.assert_not_called()
    assert cloud.calls("POST", "/schedule") == []


@pytest.mark.usefixtures("controller")
async def test_newer_command_survives_delayed_confirmation(hass, cloud):
    entry = hub_entry()
    await setup_entry(hass, entry)
    coordinator = entry.runtime_data.cloud
    cloud.apply_updates = False
    with patch("custom_components.mysa_local.cloud_coordinator.CLOUD_WRITE_SPACING", 0):
        await coordinator.async_set_temperature(DEVICE_ID, Mode.HEAT, 23)
        older = coordinator.pending[DEVICE_ID]["heat_setpoint"]
        await coordinator.async_set_temperature(DEVICE_ID, Mode.HEAT, 24)
    newer = coordinator.pending[DEVICE_ID]["heat_setpoint"]
    assert older.status == "superseded"
    cloud.state["targetHeat"]["reported"].update(
        setpoint=23, timestamp=datetime.now(UTC).timestamp()
    )
    await coordinator.async_refresh()
    assert coordinator.pending[DEVICE_ID]["heat_setpoint"] is newer
    assert newer.status == "pending"
    assert coordinator.state(DEVICE_ID).heat_setpoint == 23
    cloud.state["targetHeat"]["reported"].update(
        setpoint=24, timestamp=datetime.now(UTC).timestamp()
    )
    await coordinator.async_refresh()
    assert newer.status == "confirmed"
    assert len(cloud.updates) == 2


@pytest.mark.usefixtures("controller")
async def test_settings_storage_is_not_physical_confirmation(hass, cloud):
    entry = hub_entry()
    await setup_entry(hass, entry)
    coordinator = entry.runtime_data.cloud
    await coordinator.async_set_display_unit(DEVICE_ID, "F")
    assert coordinator.record(DEVICE_ID).display_unit == "C"
    await coordinator.async_refresh()
    command = coordinator.pending[DEVICE_ID]["display_unit"]
    assert command.status == "stored"
    assert coordinator.state(DEVICE_ID).display_unit == "F"
    assert (
        coordinator.state(DEVICE_ID).reported_observations["display_unit"].value == "C"
    )
    cloud.state["physicalInterface"]["reported"].update(
        format="F", timestamp=datetime.now(UTC).timestamp()
    )
    await coordinator.async_refresh()
    assert command.status == "confirmed"


@pytest.mark.usefixtures("controller")
async def test_optional_backoff_does_not_suppress_state(hass, cloud):
    entry = hub_entry()
    await setup_entry(hass, entry)
    coordinator = entry.runtime_data.cloud
    coordinator.mark_stale(f"firmware:{DEVICE_ID}")
    with patch.object(
        coordinator.client,
        "get_firmware_info",
        AsyncMock(side_effect=MysaRateLimitError("slow", 600)),
    ) as firmware:
        await coordinator.async_refresh()
        await coordinator.async_refresh()
    assert firmware.await_count == 1
    assert coordinator.last_update_success
    assert coordinator._due[f"firmware:{DEVICE_ID}"] > coordinator._due.get(
        "nothing", 0
    )


@pytest.mark.usefixtures("controller")
async def test_native_replacement_failure_restores_snapshot(hass, cloud):
    from datetime import time

    entry = hub_entry()
    await setup_entry(hass, entry)
    thermostat = next(iter(entry.runtime_data.thermostats.values()))
    client = thermostat.cloud.client
    actions = {day: [{"Minute": 10, "SetPoint": 22}] for day in WEEKDAYS}
    current = deepcopy(actions)

    async def native(operation, method, path, **kwargs):
        nonlocal current
        if operation == "schedules_v2":
            raise MysaNotFoundError("none")
        if method == "GET":
            return {
                "Schedules": [
                    {"Device": DEVICE_ID, "ScheduledActions": deepcopy(current)}
                ]
            }
        current = deepcopy(kwargs["body"]["ScheduledActions"])
        return None

    with (
        patch.object(client, "_legacy_request", native),
        patch.object(
            client,
            "get_devices",
            AsyncMock(return_value={DEVICE_ID: thermostat.cloud_record}),
        ),
        patch.object(
            client,
            "create_schedule",
            AsyncMock(side_effect=MysaConnectionError("failed")),
        ),
        patch("custom_components.mysa_local.cloud_coordinator.CLOUD_WRITE_SPACING", 0),
        pytest.raises(Exception, match="connection_error"),
    ):
        await async_set_schedule(
            thermostat, [{"days": ["mon"], "time": time(8), "temperature": 24}]
        )
    assert current == actions


def test_invalid_bounds_cannot_be_mixed_with_record(freezer):
    freezer.move_to(NOW)
    partial = structured(
        targetHeat={"reported": {"lockoutMin": 29, "timestamp": NOW.timestamp()}}
    )
    record = DeviceRecord.from_api(DEVICE_ID, {"MinSetpoint": 5, "MaxSetpoint": 25})
    state = reconcile(DEVICE_ID, [partial], record, None, NOW)
    assert (state.setpoint_min, state.setpoint_max) == (5, 25)
    assert state.observations["setpoint_min"].kind == "stored"


@pytest.mark.usefixtures("controller")
async def test_shared_native_v2_clear_restores_other_attachment(hass, cloud):
    entry = hub_entry()
    await setup_entry(hass, entry)
    thermostat = next(iter(entry.runtime_data.thermostats.values()))
    client = thermostat.cloud.client
    record = thermostat.cloud_record
    records = {DEVICE_ID: record, "other": DeviceRecord.from_api("other", record.raw)}
    raw = {
        "Id": "shared",
        "Owner": "owner",
        "Version": 2,
        "Events": {"0": {"10": {"BB2L": "3|22|%|%|%|%|%"}}},
    }
    attached = {DEVICE_ID, "other"}

    async def native(operation, method, path, **kwargs):
        nonlocal attached
        if operation == "schedules_v1":
            return {"Schedules": []}
        if method == "GET":
            if path.rsplit("/", 1)[-1] not in attached:
                raise MysaNotFoundError("detached")
            return deepcopy(raw)
        if method == "DELETE":
            assert kwargs["body"]["Devices"] == [DEVICE_ID]
            attached = set()  # Backend unexpectedly removed the shared record.
        else:
            assert kwargs["body"]["Schedule"]["Owner"] == "owner"
            attached.update(kwargs["body"]["Devices"])
        return None

    with (
        patch.object(client, "_legacy_request", native),
        patch.object(client, "get_devices", AsyncMock(return_value=records)),
        patch("custom_components.mysa_local.cloud_coordinator.CLOUD_WRITE_SPACING", 0),
    ):
        await async_clear_schedule(thermostat)
    assert attached == {"other"}


@pytest.mark.usefixtures("controller")
async def test_record_fallback_does_not_relax_a_reported_limit(hass, cloud):
    from homeassistant.exceptions import HomeAssistantError

    entry = hub_entry()
    await setup_entry(hass, entry)
    coordinator = entry.runtime_data.cloud
    now = datetime.now(UTC)
    partial = structured(
        now, targetHeat={"reported": {"lockoutMin": 29, "timestamp": now.timestamp()}}
    )
    record = DeviceRecord.from_api(DEVICE_ID, {"MinSetpoint": 5, "MaxSetpoint": 25})
    coordinator.data.states[DEVICE_ID] = reconcile(
        DEVICE_ID, [partial], record, None, now
    )
    with pytest.raises(HomeAssistantError, match="known device limits"):
        await coordinator.async_set_temperature(DEVICE_ID, Mode.HEAT, 20)
    assert cloud.updates == []


@pytest.mark.usefixtures("controller")
@pytest.mark.parametrize("confirm", [False, True])
async def test_delayed_write_readbacks_are_bounded(hass, cloud, freezer, confirm):
    """Reports arriving after the first read update entities without replaying writes."""
    from homeassistant.util import dt as dt_util
    from pytest_homeassistant_custom_component.common import async_fire_time_changed

    entry = hub_entry()
    await setup_entry(hass, entry)
    coordinator = entry.runtime_data.cloud
    with (
        patch.object(coordinator.client, "set_setpoint_limit", AsyncMock()) as write,
        patch.object(
            coordinator, "async_refresh", wraps=coordinator.async_refresh
        ) as read,
    ):
        await coordinator.async_set_setpoint_limit(DEVICE_ID, 5.5, minimum=True)
        freezer.tick(timedelta(seconds=3))
        async_fire_time_changed(hass)
        await hass.async_block_till_done(wait_background_tasks=True)
        assert read.await_count == 1
        assert coordinator.pending[DEVICE_ID]["setpoint_min"].status == "pending"
        if confirm:
            cloud.state["targetHeat"]["reported"].update(
                lockoutMin=5.5, timestamp=dt_util.utcnow().timestamp()
            )
        freezer.tick(timedelta(seconds=6))
        async_fire_time_changed(hass)
        await hass.async_block_till_done(wait_background_tasks=True)
        assert read.await_count == 2
        if confirm:
            assert coordinator.pending[DEVICE_ID]["setpoint_min"].status == "confirmed"
            assert coordinator.state(DEVICE_ID).setpoint_min == 5.5
        freezer.tick(timedelta(seconds=11))
        async_fire_time_changed(hass)
        await hass.async_block_till_done(wait_background_tasks=True)
        assert read.await_count == (2 if confirm else 3)
        assert coordinator._refresh_after_write is None
        write.assert_awaited_once()
