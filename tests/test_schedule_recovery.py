"""Recovery behavior when replacing a thermostat's Mysa schedule."""

import asyncio
from copy import deepcopy
from datetime import time
from typing import Any

from homeassistant.exceptions import HomeAssistantError
import pytest

from custom_components.mysa_local.api import MysaConnectionError
from custom_components.mysa_local.schedule import (
    _schedule_content_matches,
    async_clear_schedule,
    async_recover_schedule,
    async_set_schedule,
)

from .common import DEVICE_ID, HOME_ID, hub_entry
from .conftest import setup_entry


def _existing_schedule() -> dict[str, object]:
    """A schedule in the format returned by GET /schedule."""
    return {
        "id": "saved",
        "name": "Original schedule",
        "homeId": HOME_ID,
        "deviceType": "BB",
        "reusable": False,
        "deviceIds": [DEVICE_ID],
        "events": [
            {
                "executionTime": "06:00:00",
                "dayOfWeek": "1",
                "clientKey": "Mysa-app",
                "state": {"modes": {"mode": 4}, "targetHeat": {"setpoint": 20}},
            }
        ],
    }


def _mysa_get_schedule(schedule: dict[str, Any]) -> dict[str, Any]:
    """Apply the observed GET normalization without storing live device data."""
    result = deepcopy(schedule)
    device_ids = result.get("deviceIds", [])
    if result.get("name") in {
        f"Schedule-{device_id}"
        for device_id in device_ids
        if isinstance(device_id, str)
    }:
        result["name"] = "Schedule"
    for event in result.get("events", []):
        event["dayOfWeek"] = int(event["dayOfWeek"])
        event["clientKey"] = "v2-migration"
    result["events"].sort(
        key=lambda event: (int(event["dayOfWeek"]), event["executionTime"])
    )
    return result


def _use_mysa_get_schedule_shape(thermostat: Any) -> None:
    """Return the server's canonical view when schedule recovery reads state."""
    client = thermostat.cloud.client
    get_schedules = client.get_schedules

    async def transformed_get(home_id: str) -> list[dict[str, Any]]:
        schedules = await get_schedules(home_id)
        return [_mysa_get_schedule(schedule) for schedule in schedules]

    client.get_schedules = transformed_get


def _replacement_events() -> list[dict[str, object]]:
    return [{"days": ["tue"], "time": time(7, 30), "temperature": 21}]


def test_schedule_comparison_accepts_mysa_get_canonicalization() -> None:
    """A generated schedule still matches after Mysa rewrites its GET shape."""
    expected: dict[str, Any] = {
        "name": f"Schedule-{DEVICE_ID}",
        "homeId": HOME_ID,
        "deviceType": "BB",
        "reusable": False,
        "deviceIds": [DEVICE_ID],
        "events": [
            {
                "executionTime": "07:30:00",
                "dayOfWeek": "2",
                "clientKey": "HomeAssistant-0.4.0",
                "state": {
                    "modes": {"mode": 4},
                    "targetHeat": {"setpoint": 22.0},
                },
            },
            {
                "executionTime": "08:00:00",
                "dayOfWeek": "0",
                "clientKey": "HomeAssistant-0.4.0",
                "state": {
                    "modes": {"mode": 4},
                    "targetHeat": {"setpoint": 22.0},
                },
            },
        ],
    }
    actual = _mysa_get_schedule(expected)
    for event in actual["events"]:
        event["state"]["targetHeat"]["setpoint"] = 22
    actual["events"].reverse()

    assert actual["name"] == "Schedule"
    assert [event["dayOfWeek"] for event in actual["events"]] == [2, 0]
    assert all(
        isinstance(event["state"]["targetHeat"]["setpoint"], int)
        for event in actual["events"]
    )
    assert _schedule_content_matches(actual, expected)


def test_schedule_comparison_rejects_custom_name_and_event_edits() -> None:
    """Canonicalization cannot hide meaningful Mysa app changes."""
    expected = _existing_schedule()
    actual = _mysa_get_schedule(expected)
    actual["name"] = "Schedule"
    assert not _schedule_content_matches(actual, expected)

    actual = _mysa_get_schedule(expected)
    actual["events"][0]["state"]["targetHeat"]["setpoint"] = 21
    assert not _schedule_content_matches(actual, expected)

    expected["events"][0]["state"]["targetHeat"]["setpoint"] = 1.0
    actual = _mysa_get_schedule(expected)
    actual["events"][0]["state"]["targetHeat"]["setpoint"] = True
    assert not _schedule_content_matches(actual, expected)


@pytest.mark.usefixtures("controller")
async def test_failed_replacement_restores_previous_schedule(hass, cloud) -> None:
    """A failed create restores the schedule that was detached first."""
    await setup_entry(hass, hub_entry())
    cloud.schedules = [_existing_schedule()]
    thermostat = next(
        iter(
            hass.config_entries.async_entries("mysa_local")[
                0
            ].runtime_data.thermostats.values()
        )
    )
    _use_mysa_get_schedule_shape(thermostat)
    create_schedule = thermostat.cloud.client.create_schedule
    calls: list[str] = []

    async def fail_replacement_then_restore(**kwargs: object) -> str | None:
        if not calls:
            calls.append("replacement")
            raise MysaConnectionError("simulated replacement failure")
        calls.append("restore")
        return await create_schedule(**kwargs)

    thermostat.cloud.client.create_schedule = fail_replacement_then_restore

    with pytest.raises(HomeAssistantError):
        await async_set_schedule(thermostat, _replacement_events())

    assert calls == ["replacement", "restore"]
    assert len(cloud.schedules) == 1
    assert cloud.schedules[0]["deviceIds"] == [DEVICE_ID]
    assert cloud.schedules[0]["deviceType"] == "BB"
    assert _schedule_content_matches(
        _mysa_get_schedule(cloud.schedules[0]), _mysa_get_schedule(_existing_schedule())
    )


@pytest.mark.usefixtures("controller")
async def test_successful_replacement_keeps_only_new_schedule(hass, cloud) -> None:
    """A successful replacement deletes the old schedule and creates the new one."""
    await setup_entry(hass, hub_entry())
    cloud.schedules = [_existing_schedule()]
    thermostat = next(
        iter(
            hass.config_entries.async_entries("mysa_local")[
                0
            ].runtime_data.thermostats.values()
        )
    )

    await async_set_schedule(thermostat, _replacement_events())

    assert len(cloud.schedules) == 1
    assert cloud.schedules[0]["events"][0]["executionTime"] == "07:30:00"
    assert cloud.schedules[0]["events"][0]["dayOfWeek"] == "2"


@pytest.mark.usefixtures("controller")
async def test_empty_days_are_rejected_before_deleting(hass, cloud) -> None:
    """An empty generated schedule must not detach the saved one."""
    await setup_entry(hass, hub_entry())
    original = _existing_schedule()
    cloud.schedules = [original]
    thermostat = next(
        iter(
            hass.config_entries.async_entries("mysa_local")[
                0
            ].runtime_data.thermostats.values()
        )
    )

    with pytest.raises(HomeAssistantError, match="at least one event day"):
        await async_set_schedule(
            thermostat,
            [{"days": [], "time": time(7, 30), "temperature": 21}],
        )

    assert cloud.schedules == [original]


@pytest.mark.usefixtures("controller")
async def test_failed_replacement_and_rollback_reports_schedule_loss(
    hass, cloud, caplog
) -> None:
    """A rollback failure is logged and exposed as a distinct service error."""
    await setup_entry(hass, hub_entry())
    cloud.schedules = [_existing_schedule()]
    thermostat = next(
        iter(
            hass.config_entries.async_entries("mysa_local")[
                0
            ].runtime_data.thermostats.values()
        )
    )

    async def always_fail(**kwargs: object) -> str | None:
        raise MysaConnectionError("simulated schedule POST failure")

    thermostat.cloud.client.create_schedule = always_fail

    with pytest.raises(HomeAssistantError, match="saved for recovery"):
        await async_set_schedule(thermostat, _replacement_events())

    assert cloud.schedules == []
    assert "Couldn't restore a Mysa schedule" in caplog.text


@pytest.mark.usefixtures("controller")
async def test_rollback_outage_keeps_backup_for_later_recovery(hass, cloud) -> None:
    """A failed rollback survives an outage and is retried on the next operation."""
    await setup_entry(hass, hub_entry())
    cloud.schedules = [_existing_schedule()]
    thermostat = next(
        iter(
            hass.config_entries.async_entries("mysa_local")[
                0
            ].runtime_data.thermostats.values()
        )
    )
    original_create = thermostat.cloud.client.create_schedule

    async def fail_both_posts(**kwargs: object) -> str | None:
        raise MysaConnectionError("simulated cloud outage")

    thermostat.cloud.client.create_schedule = fail_both_posts
    with pytest.raises(HomeAssistantError, match="saved for recovery"):
        await async_set_schedule(thermostat, _replacement_events())

    # The DELETE succeeded, while both the attempted replacement and rollback
    # failed. The saved transaction remains in HA storage after the call.
    assert cloud.schedules == []
    with pytest.raises(HomeAssistantError, match="could not be reconciled safely"):
        await async_recover_schedule(thermostat)
    assert cloud.schedules == []

    # Restore connectivity and explicitly recover again. No new replacement
    # request is sent until the original schedule has been recovered.
    thermostat.cloud.client.create_schedule = original_create
    await async_recover_schedule(thermostat)
    assert len(cloud.schedules) == 1
    assert cloud.schedules[0]["name"] == "Original schedule"
    assert cloud.schedules[0]["events"] == _existing_schedule()["events"]
    assert cloud.schedules[0]["deviceIds"] == [DEVICE_ID]


@pytest.mark.usefixtures("controller")
async def test_reload_recovers_schedule_backup_when_cloud_returns(hass, cloud) -> None:
    """A persisted rollback is retried automatically after entry reload."""
    entry = hub_entry()
    await setup_entry(hass, entry)
    cloud.schedules = [_existing_schedule()]
    thermostat = next(iter(entry.runtime_data.thermostats.values()))

    async def fail_during_outage(**kwargs: object) -> str | None:
        raise MysaConnectionError("simulated cloud outage")

    thermostat.cloud.client.create_schedule = fail_during_outage
    with pytest.raises(HomeAssistantError, match="saved for recovery"):
        await async_set_schedule(thermostat, _replacement_events())
    assert cloud.schedules == []

    # Reload creates a fresh cloud client. The setup hook sees the durable
    # transaction after the successful initial poll and restores it in the
    # background without requiring another user schedule service call.
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert len(cloud.schedules) == 1
    assert cloud.schedules[0]["name"] == "Original schedule"
    assert cloud.schedules[0]["events"] == _existing_schedule()["events"]
    assert cloud.schedules[0]["deviceIds"] == [DEVICE_ID]


@pytest.mark.usefixtures("controller")
async def test_shared_schedule_keeps_other_devices_after_replacement(
    hass, cloud
) -> None:
    """Replacing one thermostat does not lose a shared schedule for other devices."""
    await setup_entry(hass, hub_entry())
    original = _existing_schedule()
    original["reusable"] = True
    original["deviceIds"] = [DEVICE_ID, "other-device"]
    cloud.schedules = [original]
    thermostat = next(
        iter(
            hass.config_entries.async_entries("mysa_local")[
                0
            ].runtime_data.thermostats.values()
        )
    )

    await async_set_schedule(thermostat, _replacement_events())

    other_schedule = next(
        s for s in cloud.schedules if "other-device" in s["deviceIds"]
    )
    target_schedule = next(s for s in cloud.schedules if DEVICE_ID in s["deviceIds"])
    assert other_schedule["deviceIds"] == ["other-device"]
    assert other_schedule["name"] == "Original schedule"
    assert other_schedule["events"] == original["events"]
    assert target_schedule["events"][0]["executionTime"] == "07:30:00"


@pytest.mark.usefixtures("controller")
async def test_lost_create_response_is_reconciled_without_duplicate(
    hass, cloud
) -> None:
    """A POST accepted before its connection fails is not blindly rolled back."""
    await setup_entry(hass, hub_entry())
    cloud.schedules = [_existing_schedule()]
    thermostat = next(
        iter(
            hass.config_entries.async_entries("mysa_local")[
                0
            ].runtime_data.thermostats.values()
        )
    )
    _use_mysa_get_schedule_shape(thermostat)
    create_schedule = thermostat.cloud.client.create_schedule
    first_call = True

    async def accept_then_lose_response(**kwargs: object) -> str | None:
        nonlocal first_call
        result = await create_schedule(**kwargs)
        if first_call:
            first_call = False
            raise MysaConnectionError("simulated lost POST response")
        return result

    thermostat.cloud.client.create_schedule = accept_then_lose_response
    await async_set_schedule(thermostat, _replacement_events())

    assert len(cloud.schedules) == 1
    assert cloud.schedules[0]["events"][0]["executionTime"] == "07:30:00"


@pytest.mark.usefixtures("controller")
async def test_shared_schedule_does_not_overwrite_other_device_app_edit(
    hass, cloud
) -> None:
    """An unrelated schedule on another device is preserved during rollback."""
    await setup_entry(hass, hub_entry())
    original = _existing_schedule()
    original["reusable"] = True
    original["deviceIds"] = [DEVICE_ID, "other-device"]
    cloud.schedules = [original]
    thermostat = next(
        iter(
            hass.config_entries.async_entries("mysa_local")[
                0
            ].runtime_data.thermostats.values()
        )
    )
    delete_schedule = thermostat.cloud.client.delete_schedule

    async def detach_then_add_other_device_edit(
        schedule_id: str, device_id: str
    ) -> None:
        await delete_schedule(schedule_id, device_id)
        cloud.schedules.append(
            {
                "id": "app-edited",
                "name": "Other device's new schedule",
                "homeId": HOME_ID,
                "deviceType": "BB",
                "reusable": False,
                "deviceIds": ["other-device"],
                "events": [
                    {
                        "executionTime": "08:00:00",
                        "dayOfWeek": "1",
                        "clientKey": "Mysa-app",
                        "state": {"modes": {"mode": 4}},
                    }
                ],
            }
        )

    thermostat.cloud.client.delete_schedule = detach_then_add_other_device_edit
    with pytest.raises(HomeAssistantError, match="schedule changed"):
        await async_set_schedule(thermostat, _replacement_events())

    assert any(
        s["id"] == "app-edited" and s["deviceIds"] == ["other-device"]
        for s in cloud.schedules
    )
    assert any(
        s["name"] == "Original schedule" and s["deviceIds"] == [DEVICE_ID]
        for s in cloud.schedules
    )
    assert not any(
        s["name"] == "Original schedule" and "other-device" in s["deviceIds"]
        for s in cloud.schedules
    )


@pytest.mark.usefixtures("controller")
async def test_cancelled_replacement_restores_deleted_schedule(hass, cloud) -> None:
    """Cancellation during POST still waits for a rollback attempt."""
    await setup_entry(hass, hub_entry())
    cloud.schedules = [_existing_schedule()]
    thermostat = next(
        iter(
            hass.config_entries.async_entries("mysa_local")[
                0
            ].runtime_data.thermostats.values()
        )
    )
    create_schedule = thermostat.cloud.client.create_schedule
    replacement_started = asyncio.Event()
    first_call = True

    async def cancel_first_post(**kwargs: object) -> str | None:
        nonlocal first_call
        if first_call:
            first_call = False
            replacement_started.set()
            await asyncio.Event().wait()
        return await create_schedule(**kwargs)

    thermostat.cloud.client.create_schedule = cancel_first_post
    replacement = asyncio.create_task(
        async_set_schedule(thermostat, _replacement_events())
    )
    await replacement_started.wait()
    replacement.cancel()
    with pytest.raises(asyncio.CancelledError):
        await replacement

    assert len(cloud.schedules) == 1
    assert cloud.schedules[0]["name"] == "Original schedule"
    assert cloud.schedules[0]["events"] == _existing_schedule()["events"]


@pytest.mark.usefixtures("controller")
async def test_clear_waits_for_same_device_replacement(hass, cloud) -> None:
    """A clear queued during replacement runs afterward against its new schedule."""
    await setup_entry(hass, hub_entry())
    cloud.schedules = [_existing_schedule()]
    thermostat = next(
        iter(
            hass.config_entries.async_entries("mysa_local")[
                0
            ].runtime_data.thermostats.values()
        )
    )
    original_create = thermostat.cloud.client.create_schedule
    started = asyncio.Event()
    release = asyncio.Event()

    async def pause_create(**kwargs: object) -> str | None:
        started.set()
        await release.wait()
        return await original_create(**kwargs)

    thermostat.cloud.client.create_schedule = pause_create
    replace = asyncio.create_task(async_set_schedule(thermostat, _replacement_events()))
    await started.wait()
    clear = asyncio.create_task(async_clear_schedule(thermostat))
    release.set()
    await replace
    await clear
    assert cloud.schedules == []
