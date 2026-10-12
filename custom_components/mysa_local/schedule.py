"""Home Assistant adapters for Mysa schedule and hold workflows."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from datetime import datetime
from functools import partial
from typing import TYPE_CHECKING, Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util
from homeassistant.util.hass_dict import HassKey
from mysa_client.cloud import Mode, MysaNotFoundError
from mysa_client.cloud.holds import async_apply_hold, resolve_hold
from mysa_client.cloud.schedule_workflow import (
    ScheduleWorkflow,
    ScheduleWorkflowError,
    build_schedule_events,
    schedule_content_matches,
)

from .cloud_coordinator import translated_error
from .const import DOMAIN, LOGGER, VERSION

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
    cloud = thermostat.require_legacy_cloud()
    if until is not None:
        until = dt_util.as_utc(
            until
            if until.tzinfo
            else until.replace(tzinfo=dt_util.get_default_time_zone())
        )
        if until <= dt_util.utcnow():
            raise translated_error("hold_in_past")
    command = resolve_hold(
        requested_mode=mode,
        current_mode=thermostat.mode,
        requested_temperature=temperature,
        current_temperature=thermostat.target_temperature,
        until=until,
    )
    device_id = thermostat.device_id

    async def set_hold(state: Mapping[str, Any], end_time: datetime | None) -> None:
        async def _set() -> None:
            try:
                await cloud.client.set_hold(device_id, state, until=end_time)
            except MysaNotFoundError as err:
                # The device must be following a schedule before Mysa accepts a hold.
                raise translated_error(
                    "hold_needs_schedule", name=thermostat.name
                ) from err

        await cloud.async_call(
            _set,
            stale=[f"hold:{device_id}"],
            device_id=device_id,
            operation="set_hold",
            requested={
                "temperature": command.temperature,
                "mode": command.mode,
                "until": end_time,
            },
        )

    await async_apply_hold(
        command,
        set_hold=set_hold,
        current_mode=lambda: thermostat.mode,
        current_temperature=lambda: thermostat.target_temperature,
        set_mode=thermostat.async_set_mode,
        set_temperature=thermostat.async_set_temperature,
    )


async def async_resume_schedule(thermostat: Thermostat) -> None:
    """End a hold so the thermostat follows its schedule again."""
    cloud = thermostat.require_legacy_cloud()
    device_id = thermostat.device_id
    await cloud.async_call(
        lambda: cloud.client.resume_schedule(device_id),
        stale=[f"hold:{device_id}"],
        device_id=device_id,
        operation="resume_schedule",
    )


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


class _HAScheduleTransport:
    """Instrument workflow writes through the cloud coordinator."""

    def __init__(self, thermostat: Thermostat) -> None:
        self._cloud = thermostat.require_legacy_cloud()

    async def get_all_schedules(self, home_id: str) -> list[dict[str, Any]]:
        return await self._cloud.client.get_all_schedules(home_id)

    async def remove_schedule(
        self, schedule: Mapping[str, Any], device_id: str
    ) -> None:
        await self._cloud.async_call(
            partial(self._cloud.client.remove_schedule, schedule, device_id),
            device_id=device_id,
            operation="schedule_write",
        )

    async def create_schedule(self, payload: Mapping[str, Any], device_id: str) -> None:
        if payload.get("origin"):
            await self._cloud.async_call(
                partial(self._cloud.client.restore_native_schedule, payload),
                device_id=device_id,
                operation="schedule_write",
            )
            return
        await self._cloud.async_call(
            partial(
                self._cloud.client.create_schedule,
                home_id=payload["homeId"],
                device_id=device_id,
                device_type=payload["deviceType"],
                events=payload["events"],
                name=payload["name"],
                reusable=payload["reusable"],
                device_ids=payload["deviceIds"],
            ),
            stale=[f"hold:{device_id}"],
            requested={"event_count": len(payload["events"])},
            device_id=device_id,
            operation="schedule_write",
        )


def _workflow(thermostat: Thermostat, store: Store[dict[str, Any]]) -> ScheduleWorkflow:
    return ScheduleWorkflow(
        transport=_HAScheduleTransport(thermostat),
        journal=store,
        device_id=thermostat.device_id,
        home_id=_home_id(thermostat),
        device_type=_schedule_device_type(thermostat),
        client_key=CLIENT_KEY,
    )


def _raise_ha_error(err: ScheduleWorkflowError) -> HomeAssistantError:
    return HomeAssistantError(err.message)


async def _async_recover_after_cancel(
    thermostat: Thermostat, workflow: ScheduleWorkflow, operation: str
) -> None:
    """Finish durable reconciliation in an HA-managed task after cancellation."""
    try:
        await workflow.async_recover()
    except Exception:  # noqa: BLE001 - retain journal and report the recovery need
        LOGGER.error(
            "Couldn't finish cancelled schedule %s for %s; the durable backup was kept",
            operation,
            thermostat.history.label(thermostat.device_id),
        )


async def async_recover_schedule(thermostat: Thermostat) -> None:
    """Recover a durable schedule backup, for startup or a repair action."""
    store = _schedule_store(thermostat.hass, thermostat.device_id)
    async with _schedule_lock(thermostat.hass, thermostat.device_id):
        try:
            await _workflow(thermostat, store).async_recover()
        except ScheduleWorkflowError as err:
            raise _raise_ha_error(err) from err


def build_events(
    events: list[dict[str, Any]], default_mode: Mode
) -> list[dict[str, Any]]:
    """Turn the service action's events into Mysa schedule events."""
    return build_schedule_events(
        events, default_mode=default_mode, client_key=CLIENT_KEY
    )


def _schedule_content_matches(actual: dict[str, Any], expected: dict[str, Any]) -> bool:
    """Compatibility name retained for the HA recovery tests."""
    return schedule_content_matches(actual, expected)


async def async_set_schedule(
    thermostat: Thermostat, events: list[dict[str, Any]]
) -> None:
    """Replace the thermostat's weekly schedule."""
    encoded_events = build_events(events, Mode.HEAT)
    if not encoded_events:
        raise HomeAssistantError("A schedule must include at least one event day.")
    store = _schedule_store(thermostat.hass, thermostat.device_id)
    async with _schedule_lock(thermostat.hass, thermostat.device_id):
        workflow = _workflow(thermostat, store)
        try:
            await workflow.async_set_encoded(encoded_events)
        except asyncio.CancelledError:
            recovery_task = thermostat.hass.async_create_task(
                _async_recover_after_cancel(thermostat, workflow, "replacement"),
                f"mysa schedule recovery {thermostat.device_id}",
            )
            try:
                await asyncio.shield(recovery_task)
            except asyncio.CancelledError:
                await recovery_task
            raise
        except ScheduleWorkflowError as err:
            if err.code == "schedule-replacement-failed":
                LOGGER.error(
                    "Couldn't restore a Mysa schedule after a failed replacement "
                    "for %s; a durable backup was kept",
                    thermostat.history.label(thermostat.device_id),
                )
                raise HomeAssistantError(
                    f"Replacing the Mysa schedule for {thermostat.name} failed. "
                    "The prior schedule is saved for recovery; review the Mysa app "
                    "and try again when the cloud is available."
                ) from err
            raise _raise_ha_error(err) from err


async def async_clear_schedule(thermostat: Thermostat) -> None:
    """Delete every schedule attached to the thermostat."""
    store = _schedule_store(thermostat.hass, thermostat.device_id)
    async with _schedule_lock(thermostat.hass, thermostat.device_id):
        workflow = _workflow(thermostat, store)
        try:
            await workflow.async_clear()
        except asyncio.CancelledError:
            recovery_task = thermostat.hass.async_create_task(
                _async_recover_after_cancel(thermostat, workflow, "clear"),
                f"mysa schedule clear recovery {thermostat.device_id}",
            )
            try:
                await asyncio.shield(recovery_task)
            except asyncio.CancelledError:
                await recovery_task
            raise
        except ScheduleWorkflowError as err:
            raise _raise_ha_error(err) from err
