"""Holds and weekly schedules (cloud features)."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from copy import deepcopy
from datetime import datetime
from functools import partial
import json
import math
from typing import TYPE_CHECKING, Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util
from homeassistant.util.hass_dict import HassKey

from .cloud_coordinator import translated_error
from .const import DOMAIN, LOGGER, VERSION
from .protocols.cloud import (
    Mode,
    MysaNotFoundError,
    ScheduleEventSpec,
    encode_schedule_events,
    make_state,
)

if TYPE_CHECKING:
    from .thermostat import Thermostat

CLIENT_KEY = f"HomeAssistant-{VERSION}"
_SCHEDULE_STORES: HassKey[dict[str, Store[dict[str, Any]]]] = HassKey(
    f"{DOMAIN}_schedule_stores"
)
_SCHEDULE_LOCKS: HassKey[dict[str, asyncio.Lock]] = HassKey(f"{DOMAIN}_schedule_locks")


def _home_id(thermostat: Thermostat) -> str:
    record = thermostat.cloud_record
    if record is None or not record.home_id:
        raise translated_error("cloud_required", name=thermostat.name)
    return record.home_id


async def async_hold(
    hass: HomeAssistant,
    thermostat: Thermostat,
    temperature: float | None,
    mode: Mode | None,
    until: datetime | None,
) -> None:
    """Hold a state over the schedule, permanently or until a time."""
    cloud = thermostat.require_cloud()
    mode = mode or thermostat.mode or Mode.HEAT
    if temperature is None:
        temperature = thermostat.target_temperature
    if until is not None:
        until = dt_util.as_utc(
            until
            if until.tzinfo
            else until.replace(tzinfo=dt_util.get_default_time_zone())
        )
        if until <= dt_util.utcnow():
            raise translated_error("hold_in_past")
    device_id = thermostat.device_id
    state = make_state(mode, temperature)

    async def _hold() -> None:
        try:
            await cloud.client.set_hold(device_id, state, until=until)
        except MysaNotFoundError as err:
            # "Device ... is not following any schedule. Cannot create a hold."
            raise translated_error("hold_needs_schedule", name=thermostat.name) from err

    await cloud.async_call(_hold, stale=[f"hold:{device_id}"])
    # Mysa applies the held state from its own scheduler once the hold starts;
    # apply it now as well (local first), so the thermostat changes right away.
    if thermostat.mode != mode:
        await thermostat.async_set_mode(mode)
    if temperature is not None and thermostat.target_temperature != temperature:
        await thermostat.async_set_temperature(temperature)


async def async_resume_schedule(thermostat: Thermostat) -> None:
    """End a hold so the thermostat follows its schedule again."""
    cloud = thermostat.require_cloud()
    device_id = thermostat.device_id
    await cloud.async_call(
        lambda: cloud.client.resume_schedule(device_id), stale=[f"hold:{device_id}"]
    )


async def _async_schedules_for(thermostat: Thermostat) -> list[dict[str, Any]]:
    cloud = thermostat.require_cloud()
    schedules = await cloud.client.get_schedules(_home_id(thermostat))
    return [s for s in schedules if thermostat.device_id in (s.get("deviceIds") or [])]


def _schedule_store(hass: HomeAssistant, device_id: str) -> Store[dict[str, Any]]:
    """Get private, restart-persistent storage for an unfinished schedule edit."""
    stores = hass.data.setdefault(_SCHEDULE_STORES, {})
    return stores.setdefault(
        device_id,
        Store(hass, 1, f"{DOMAIN}_schedule_recovery_{device_id}", private=True),
    )


def _schedule_lock(hass: HomeAssistant, device_id: str) -> asyncio.Lock:
    """Serialize schedule edits for a device, including clear versus replace."""
    locks = hass.data.setdefault(_SCHEDULE_LOCKS, {})
    return locks.setdefault(device_id, asyncio.Lock())


def _schedule_device_type(thermostat: Thermostat) -> str:
    """The Mysa schedule type for one thermostat."""
    return (thermostat.model or "").split("-", 1)[0] or "BB"


def _validate_replaceable(schedules: list[dict[str, Any]]) -> None:
    """Check we can delete and later recreate every existing schedule."""
    for schedule in schedules:
        if schedule.get("id") is None:
            raise HomeAssistantError(
                "Mysa returned a schedule without an ID; it can't be safely replaced."
            )
        events = schedule.get("events")
        if not isinstance(events, list) or any(
            not isinstance(event, dict) for event in events
        ):
            raise HomeAssistantError(
                "Mysa returned a schedule that can't be backed up; it wasn't changed."
            )
        device_ids = schedule.get("deviceIds")
        if not isinstance(device_ids, list) or any(
            not isinstance(device_id, str) for device_id in device_ids
        ):
            raise HomeAssistantError(
                "Mysa returned a schedule without valid device IDs; it wasn't changed."
            )
        if not isinstance(schedule.get("name"), str) or not isinstance(
            schedule.get("reusable"), bool
        ):
            raise HomeAssistantError(
                "Mysa returned a schedule without restorable metadata; it wasn't changed."
            )


def _schedule_content_matches(actual: dict[str, Any], expected: dict[str, Any]) -> bool:
    """Compare semantic schedule content across Mysa's GET normalization."""
    actual_name = actual.get("name")
    expected_name = expected.get("name")
    generated_name = (
        isinstance(expected_name, str)
        and isinstance(expected.get("deviceIds"), list)
        and any(
            isinstance(device_id, str) and expected_name == f"Schedule-{device_id}"
            for device_id in expected["deviceIds"]
        )
    )
    names_match = actual_name == expected_name or (
        generated_name and actual_name == "Schedule"
    )
    actual_events = _canonical_schedule_events(actual.get("events"))
    expected_events = _canonical_schedule_events(expected.get("events"))
    return (
        names_match
        and all(
            actual.get(key) == expected.get(key)
            for key in ("homeId", "deviceType", "reusable")
        )
        and actual_events is not None
        and actual_events == expected_events
    )


def _canonical_json_value(value: Any) -> Any:
    """Normalize integral floats recursively without conflating booleans."""
    if isinstance(value, bool) or value is None or isinstance(value, (str, int)):
        return value
    if isinstance(value, float):
        return int(value) if math.isfinite(value) and value.is_integer() else value
    if isinstance(value, list):
        return [_canonical_json_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _canonical_json_value(item) for key, item in value.items()}
    return value


def _canonical_schedule_events(events: Any) -> tuple[str, ...] | None:
    """Serialize events independent of order, numeric type, or client marker."""
    if not isinstance(events, list):
        return None

    canonical: list[str] = []
    for event in events:
        if not isinstance(event, dict):
            return None
        semantic = _canonical_json_value(
            {key: value for key, value in event.items() if key != "clientKey"}
        )
        day = semantic.get("dayOfWeek")
        if isinstance(day, int) and not isinstance(day, bool):
            semantic["dayOfWeek"] = str(day)
        elif isinstance(day, str):
            with suppress(ValueError):
                semantic["dayOfWeek"] = str(int(day))
        try:
            canonical.append(
                json.dumps(
                    semantic,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                )
            )
        except TypeError, ValueError:
            return None
    return tuple(sorted(canonical))


def _schedule_matches(
    actual: dict[str, Any], expected: dict[str, Any], device_ids: list[str]
) -> bool:
    """Compare schedule content and its exact set of attachments."""
    actual_ids = actual.get("deviceIds")
    return (
        _schedule_content_matches(actual, expected)
        and isinstance(actual_ids, list)
        and set(actual_ids) == set(device_ids)
    )


def _restorable_schedule(
    schedule: dict[str, Any], home_id: str, device_type: str
) -> dict[str, Any]:
    """Extract the fields accepted by POST /schedule from a GET record."""
    return {
        "name": schedule["name"],
        "homeId": str(schedule.get("homeId") or home_id),
        "deviceType": str(schedule.get("deviceType") or device_type),
        "reusable": schedule["reusable"],
        "deviceIds": list(schedule["deviceIds"]),
        "events": deepcopy(schedule["events"]),
    }


def _new_schedule_payload(
    thermostat: Thermostat, home_id: str, events: list[dict[str, Any]]
) -> dict[str, Any]:
    return {
        "name": f"Schedule-{thermostat.device_id}",
        "homeId": home_id,
        "deviceType": _schedule_device_type(thermostat),
        "reusable": False,
        "deviceIds": [thermostat.device_id],
        "events": deepcopy(events),
    }


async def _async_create_schedule(
    thermostat: Thermostat, payload: dict[str, Any]
) -> None:
    cloud = thermostat.require_cloud()
    await cloud.async_call(
        partial(
            cloud.client.create_schedule,
            home_id=payload["homeId"],
            device_id=thermostat.device_id,
            device_type=payload["deviceType"],
            events=payload["events"],
            name=payload["name"],
            reusable=payload["reusable"],
            device_ids=payload["deviceIds"],
        ),
        stale=[f"hold:{thermostat.device_id}"],
    )


async def _async_all_schedules(
    thermostat: Thermostat, home_id: str
) -> list[dict[str, Any]]:
    cloud = thermostat.require_cloud()
    return await cloud.client.get_schedules(home_id)


async def _async_preserve_other_devices(
    thermostat: Thermostat,
    schedule: dict[str, Any],
    home_id: str,
    all_schedules: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Recreate a detached shared schedule for any other devices still using it."""
    target_id = thermostat.device_id
    other_ids = [
        device_id for device_id in schedule["deviceIds"] if device_id != target_id
    ]
    if not other_ids:
        return all_schedules
    found_ids: set[str] = set()
    for actual in all_schedules:
        ids = actual.get("deviceIds")
        if isinstance(ids, list) and _schedule_content_matches(actual, schedule):
            found_ids.update(device_id for device_id in ids if device_id in other_ids)
    missing_ids = [device_id for device_id in other_ids if device_id not in found_ids]
    if not missing_ids:
        return all_schedules
    changed_ids = {
        device_id
        for actual in all_schedules
        if not _schedule_content_matches(actual, schedule)
        for device_id in (actual.get("deviceIds") or [])
        if device_id in missing_ids
    }
    if changed_ids:
        raise HomeAssistantError(
            "A shared Mysa schedule disappeared while another device's schedule "
            "changed. The saved schedule was kept for review."
        )
    payload = _restorable_schedule(schedule, home_id, _schedule_device_type(thermostat))
    payload["deviceIds"] = missing_ids
    await _async_create_schedule(thermostat, payload)
    return await _async_all_schedules(thermostat, home_id)


async def _async_restore_replacement(
    thermostat: Thermostat,
    transaction: dict[str, Any],
    store: Store[dict[str, Any]],
) -> str:
    """Restore deleted schedules after a failed replacement, retaining backup on uncertainty."""
    cloud = thermostat.require_cloud()
    home_id = str(transaction["home_id"])
    old_schedules = transaction["old_schedules"]
    new_payload = transaction["new_schedule"]
    device_id = thermostat.device_id

    # If the new POST succeeded but its response was lost, accept the requested
    # schedule only when no original schedule is still attached to this device.
    actual = await _async_all_schedules(thermostat, home_id)
    attached = [s for s in actual if device_id in (s.get("deviceIds") or [])]
    new_is_attached = any(
        _schedule_matches(s, new_payload, [device_id]) for s in attached
    )
    if new_is_attached:
        originals_still_attached = [
            s
            for s in attached
            if any(
                str(s.get("id")) == str(old.get("id"))
                and _schedule_matches(s, old, list(old["deviceIds"]))
                for old in old_schedules
            )
        ]
        if originals_still_attached:
            # The create may have landed while one or more deletes did not. Only
            # detach records that still exactly match the saved pre-edit state.
            for old in originals_still_attached:
                await cloud.async_call(
                    partial(cloud.client.delete_schedule, str(old["id"]), device_id)
                )
            actual = await _async_all_schedules(thermostat, home_id)
            attached = [s for s in actual if device_id in (s.get("deviceIds") or [])]
            if any(
                _schedule_matches(s, old, list(old["deviceIds"]))
                for s in attached
                for old in old_schedules
            ):
                return "uncertain"
        # Do not clear a backup if an unrelated schedule appeared while HA was
        # offline. This avoids silently combining a user's app edit with ours.
        if any(
            not _schedule_matches(s, new_payload, [device_id])
            and not any(
                _schedule_matches(s, old, list(old["deviceIds"]))
                for old in old_schedules
            )
            for s in attached
        ):
            return "uncertain"
        # The original schedules' other device attachments must survive too.
        for old in old_schedules:
            actual = await _async_preserve_other_devices(
                thermostat, old, home_id, actual
            )
        await store.async_remove()
        return "completed"

    # A schedule created or edited in the Mysa app since the backup is not ours
    # to replace. Keep the durable snapshot and ask the user to review it.
    expected_old = [
        s
        for s in attached
        if any(
            _schedule_matches(s, old, list(old["deviceIds"])) for old in old_schedules
        )
    ]
    if len(expected_old) != len(attached):
        return "uncertain"

    errors: list[HomeAssistantError] = []
    for old in old_schedules:
        if any(
            _schedule_content_matches(s, old)
            and device_id in (s.get("deviceIds") or [])
            for s in attached
        ):
            continue
        try:
            old_payload = _restorable_schedule(
                old, home_id, _schedule_device_type(thermostat)
            )
            # Restore the target, and any other device that now has no schedule.
            # Leave devices with a different schedule alone so a Mysa app change
            # made while Home Assistant was offline isn't overwritten or joined.
            other_ids = [item for item in old_payload["deviceIds"] if item != device_id]
            represented_elsewhere = {
                other_id
                for s in actual
                if _schedule_content_matches(s, old)
                for other_id in (s.get("deviceIds") or [])
                if other_id in other_ids
            }
            scheduled_elsewhere = {
                other_id
                for s in actual
                for other_id in (s.get("deviceIds") or [])
                if other_id in other_ids
            }
            missing_other_ids = [
                other_id
                for other_id in other_ids
                if other_id not in represented_elsewhere
                and other_id not in scheduled_elsewhere
            ]
            old_payload["deviceIds"] = [device_id, *missing_other_ids]
            await _async_create_schedule(thermostat, old_payload)
            # Refresh the live view before handling another saved schedule. This
            # also makes a retry after a later failure idempotent.
            actual = await _async_all_schedules(thermostat, home_id)
            attached = [s for s in actual if device_id in (s.get("deviceIds") or [])]
        except HomeAssistantError as err:
            errors.append(err)
            LOGGER.error(
                "Couldn't restore a Mysa schedule for %s; a durable backup was kept",
                thermostat.name,
                exc_info=err,
            )
            break

    if errors:
        return "uncertain"
    # Verify the backup's original schedules are represented before removing it.
    if any(
        not any(
            _schedule_content_matches(s, old)
            and device_id in (s.get("deviceIds") or [])
            for s in attached
        )
        for old in old_schedules
    ):
        return "uncertain"
    await store.async_remove()
    return "restored"


async def _async_recover_pending(
    thermostat: Thermostat,
    store: Store[dict[str, Any]],
    *,
    for_clear: bool = False,
) -> None:
    """Finish an interrupted clear or safely roll back an interrupted replacement."""
    transaction = await store.async_load()
    if not isinstance(transaction, dict):
        return
    operation = transaction.get("operation")
    if operation == "clear":
        await _async_finish_clear(thermostat, transaction, store)
        return
    if operation != "replace":
        raise HomeAssistantError(
            "An unfinished Mysa schedule change has an unknown backup format. "
            "Review the Mysa schedule before making another change."
        )
    if await _async_restore_replacement(thermostat, transaction, store) != "uncertain":
        return
    if for_clear:
        # Clear is an explicit request to remove the device's schedule. Keeping
        # an unresolved replacement backup is safer than deleting an app edit.
        raise HomeAssistantError(
            "An earlier Mysa schedule change could not be reconciled safely. "
            "Review the schedule in the Mysa app before clearing it."
        )
    raise HomeAssistantError(
        "An earlier Mysa schedule change could not be reconciled safely. "
        "Review the schedule in the Mysa app before replacing it."
    )


async def _async_finish_clear(
    thermostat: Thermostat,
    transaction: dict[str, Any],
    store: Store[dict[str, Any]],
) -> None:
    """Continue an interrupted clear without deleting schedules added in the app later."""
    cloud = thermostat.require_cloud()
    home_id = str(transaction["home_id"])
    old_schedules = transaction["old_schedules"]
    device_id = thermostat.device_id
    actual = await _async_all_schedules(thermostat, home_id)
    attached = [s for s in actual if device_id in (s.get("deviceIds") or [])]

    # A schedule not represented in the pre-clear snapshot may have been added
    # later in the Mysa app. Keep the backup and leave that change untouched.
    if any(
        not any(
            str(s.get("id")) == str(old.get("id"))
            and _schedule_matches(s, old, list(old["deviceIds"]))
            for old in old_schedules
        )
        for s in attached
    ):
        raise HomeAssistantError(
            "A new Mysa schedule appeared while an earlier clear was interrupted. "
            "Review the schedule in the Mysa app before clearing it again."
        )

    for schedule in attached:
        await cloud.async_call(
            partial(cloud.client.delete_schedule, str(schedule["id"]), device_id)
        )

    actual = await _async_all_schedules(thermostat, home_id)
    attached = [s for s in actual if device_id in (s.get("deviceIds") or [])]
    if attached:
        raise HomeAssistantError(
            "Mysa still reports a schedule attached after clearing it. "
            "A recovery backup was kept; try again when the cloud is available."
        )
    for old in old_schedules:
        actual = await _async_preserve_other_devices(thermostat, old, home_id, actual)
    await store.async_remove()


async def async_recover_schedule(thermostat: Thermostat) -> None:
    """Recover a durable schedule backup, for startup recovery or a repair action."""
    store = _schedule_store(thermostat.hass, thermostat.device_id)
    async with _schedule_lock(thermostat.hass, thermostat.device_id):
        await _async_recover_pending(thermostat, store)


def build_events(
    events: list[dict[str, Any]], default_mode: Mode
) -> list[dict[str, Any]]:
    """Turn the action's events into Mysa schedule events."""
    return encode_schedule_events(
        [
            ScheduleEventSpec(
                days=tuple(event["days"]),
                at=event["time"],
                mode=Mode[str(event.get("hvac_mode", default_mode.name)).upper()],
                temperature=event.get("temperature"),
                client_key=CLIENT_KEY,
            )
            for event in events
        ]
    )


async def async_set_schedule(
    thermostat: Thermostat, events: list[dict[str, Any]]
) -> None:
    """Replace the thermostat's weekly schedule."""
    device_id = thermostat.device_id
    mysa_events = build_events(events, Mode.HEAT)
    if not mysa_events:
        raise HomeAssistantError("A schedule must include at least one event day.")
    home_id = _home_id(thermostat)
    store = _schedule_store(thermostat.hass, device_id)
    async with _schedule_lock(thermostat.hass, device_id):
        await _async_recover_pending(thermostat, store)
        existing = await _async_schedules_for(thermostat)
        _validate_replaceable(existing)
        new_payload = _new_schedule_payload(thermostat, home_id, mysa_events)
        transaction: dict[str, Any] = {
            "operation": "replace",
            "home_id": home_id,
            "device_id": device_id,
            "old_schedules": deepcopy(existing),
            "new_schedule": new_payload,
        }
        # Save before any DELETE. A crash, cancellation, or lost response can
        # then be reconciled on the next operation or after Home Assistant restarts.
        await store.async_save(transaction)

        async def _replace() -> None:
            cloud = thermostat.require_cloud()
            for schedule in existing:
                await cloud.async_call(
                    partial(
                        cloud.client.delete_schedule,
                        str(schedule["id"]),
                        device_id,
                    )
                )

            actual = await _async_all_schedules(thermostat, home_id)
            attached = [
                schedule
                for schedule in actual
                if device_id in (schedule.get("deviceIds") or [])
            ]
            if attached:
                raise HomeAssistantError(
                    "Mysa still reports an old schedule attached after deleting it."
                )
            # Some Mysa schedule records are shared. The detach endpoint may
            # remove the whole record, so recreate any other devices' copy.
            for schedule in existing:
                actual = await _async_preserve_other_devices(
                    thermostat, schedule, home_id, actual
                )
            await _async_create_schedule(thermostat, new_payload)

        try:
            await _replace()
            await store.async_remove()
        except asyncio.CancelledError:
            # Let the durable rollback/commit reconciliation finish even though
            # the service caller was cancelled during an in-flight API request.
            recovery_task = thermostat.hass.async_create_task(
                _async_restore_replacement(thermostat, transaction, store),
                f"mysa schedule recovery {device_id}",
            )
            try:
                await asyncio.shield(recovery_task)
            except asyncio.CancelledError:
                try:
                    await recovery_task
                except Exception:  # noqa: BLE001 - keep the recovery backup on any failure
                    LOGGER.exception(
                        "Couldn't finish cancelled schedule recovery for %s; "
                        "the durable backup was kept",
                        thermostat.name,
                    )
            except Exception:  # noqa: BLE001 - keep the recovery backup on any failure
                LOGGER.exception(
                    "Couldn't finish cancelled schedule recovery for %s; "
                    "the durable backup was kept",
                    thermostat.name,
                )
            raise
        except Exception as err:
            try:
                outcome = await _async_restore_replacement(
                    thermostat, transaction, store
                )
            except Exception as recovery_err:  # noqa: BLE001 - reconcile every failed write
                LOGGER.error(
                    "Couldn't reconcile the failed Mysa schedule replacement for %s; "
                    "a durable backup was kept",
                    thermostat.name,
                    exc_info=recovery_err,
                )
                outcome = "uncertain"
            if outcome == "completed":
                # The POST may have succeeded even though its response was lost.
                return
            if outcome == "restored":
                raise
            raise HomeAssistantError(
                f"Replacing the Mysa schedule for {thermostat.name} failed. "
                "The prior schedule is saved for recovery; review the Mysa app "
                "and try again when the cloud is available."
            ) from err


async def async_clear_schedule(thermostat: Thermostat) -> None:
    """Delete every schedule attached to the thermostat."""
    device_id = thermostat.device_id
    home_id = _home_id(thermostat)
    store = _schedule_store(thermostat.hass, device_id)
    async with _schedule_lock(thermostat.hass, device_id):
        await _async_recover_pending(thermostat, store, for_clear=True)
        existing = await _async_schedules_for(thermostat)
        _validate_replaceable(existing)
        if not existing:
            return
        transaction: dict[str, Any] = {
            "operation": "clear",
            "home_id": home_id,
            "device_id": device_id,
            "old_schedules": deepcopy(existing),
        }
        await store.async_save(transaction)
        try:
            await _async_finish_clear(thermostat, transaction, store)
        except asyncio.CancelledError:
            recovery_task = thermostat.hass.async_create_task(
                _async_finish_clear(thermostat, transaction, store),
                f"mysa schedule clear recovery {device_id}",
            )
            try:
                await asyncio.shield(recovery_task)
            except asyncio.CancelledError:
                try:
                    await recovery_task
                except Exception:  # noqa: BLE001 - keep the recovery backup on any failure
                    LOGGER.exception(
                        "Couldn't finish cancelled schedule clear for %s; "
                        "the durable backup was kept",
                        thermostat.name,
                    )
            except Exception:  # noqa: BLE001 - keep the recovery backup on any failure
                LOGGER.exception(
                    "Couldn't finish cancelled schedule clear for %s; "
                    "the durable backup was kept",
                    thermostat.name,
                )
            raise
