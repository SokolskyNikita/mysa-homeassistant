"""Serialize thermostat commands and reconcile delayed local writes.

The router chooses the transport; protocol clients own encoding and I/O. A local
write that outlives its timeout stays alive until the request settles, so the
HomeKit session remains usable and newer cloud commands can be reasserted.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
import time
from typing import Any

from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util

from .cloud_coordinator import MysaCloudCoordinator, translated_error
from .const import LOCAL_WRITE_TIMEOUT, LOGGER
from .event_history import EventHistory, error_category
from .local_adapter import LOCAL_ERRORS, LocalThermostat, LocalWriteRejectedError
from .protocols.cloud import (
    DeviceState,
    MysaConnectionError,
    MysaError,
    MysaRateLimitError,
)
from .protocols.homekit import Field


class CommandRouter:
    """Own ordering, in-flight writes and fallback for one thermostat."""

    def __init__(
        self,
        hass: HomeAssistant,
        *,
        device_id: str,
        name: str,
        local: LocalThermostat | None,
        cloud_getter: Callable[[], MysaCloudCoordinator | None],
        notify: CALLBACK_TYPE,
        history: EventHistory | None = None,
    ) -> None:
        """Attach existing clients without starting any network activity."""
        self.history = history if history is not None else EventHistory()
        self.hass = hass
        self.device_id = device_id
        self.name = name
        self.local = local
        self._cloud_getter = cloud_getter
        self._notify = notify
        self._write_lock = asyncio.Lock()
        self._pending_local: asyncio.Task[None] | None = None
        self._pending_values: dict[str, Any] = {}
        self._late_local: asyncio.Task[None] | None = None
        self._reconcile: dict[
            str, Callable[[MysaCloudCoordinator], Awaitable[None]]
        ] = {}
        self._reconcile_ids: dict[str, str] = {}
        self._reconcile_task: asyncio.Task[None] | None = None
        self._stopped = False

    @property
    def cloud(self) -> MysaCloudCoordinator | None:
        """The current coordinator, including when cloud access is disabled."""
        return self._cloud_getter()

    @property
    def local_ok(self) -> bool:
        """Whether local state can be trusted after any late-write recovery."""
        return (
            self.local is not None
            and self.local.available
            and self.local.ready
            and not self._reconcile
        )

    @callback
    def stop(self) -> None:
        """Stop reconciliation when this thermostat's entry is unloaded."""
        self._stopped = True
        if self._reconcile_task is not None:
            self._reconcile_task.cancel()

    async def async_stop(self) -> None:
        """Close the HAP session before cancelling and reconciling pending writes."""
        self.stop()
        if self._reconcile_task is not None:
            await asyncio.gather(self._reconcile_task, return_exceptions=True)
        async with self._write_lock:
            if self.local is not None:
                await self.local.async_stop()
            if self._pending_local is not None and not self._pending_local.done():
                # Normal timeouts leave requests alone to preserve HAP framing.
                # On unload the session is closed first, so cancelling cannot
                # corrupt a later request on that session.
                self._pending_local.cancel()
                await asyncio.gather(self._pending_local, return_exceptions=True)
        await self._async_reconcile(allow_stopped=True)

    @callback
    def _local_write_done(self, task: asyncio.Task[None]) -> None:
        """Observe late failures and reconcile commands after the request settles."""
        if not task.cancelled():
            error = task.exception()
            if task is self._late_local:
                self.history.record(
                    "request",
                    operation="local_write",
                    transport="homekit",
                    status="failed" if error else "accepted",
                    detail="late_response",
                    error=error_category(error) if error else "none",
                )
        if task is self._late_local:
            self._late_local = None
        self.schedule_reconciliation()

    @callback
    def schedule_reconciliation(self) -> None:
        """Retry any newer cloud intent after an outstanding local request settles."""
        if (
            self._stopped
            or self.hass.is_stopping
            or self._pending_local is None
            or not self._pending_local.done()
            or (self._reconcile_task is not None and not self._reconcile_task.done())
        ):
            return
        self._reconcile_task = self.hass.async_create_background_task(
            self._async_reconcile(), f"mysa {self.name} reconcile local write"
        )

    async def _async_reconcile(self, *, allow_stopped: bool = False) -> None:
        """Reassert newer successful commands after an older local write finishes."""
        async with self._write_lock:
            if (
                (self._stopped and not allow_stopped)
                or self._pending_local is None
                or not self._pending_local.done()
            ):
                return
            if self._reconcile:
                cloud = self.cloud
                if cloud is None:
                    return
                assert self.local is not None
                self.local.mark_unavailable("confirming newer cloud commands")
                try:
                    writes: list[Callable[[MysaCloudCoordinator], Awaitable[None]]] = []
                    for write in self._reconcile.values():
                        if not any(write is pending for pending in writes):
                            writes.append(write)
                    for write in writes:
                        correlation_id = next(
                            self._reconcile_ids[key]
                            for key, pending in self._reconcile.items()
                            if pending is write
                        )
                        with self.history.command(correlation_id, self.device_id):
                            self.history.record(
                                "retry", operation="reconcile", transport="cloud"
                            )
                            await write(cloud)
                        for key, pending in list(self._reconcile.items()):
                            if pending is write:
                                del self._reconcile[key]
                                self._reconcile_ids.pop(key, None)
                except HomeAssistantError as err:
                    # Keep the desired commands and retry on the next update.
                    LOGGER.warning(
                        "%s: couldn't reconcile a late write: %s",
                        self.history.label(self.device_id),
                        type(err).__name__,
                    )
                    return
            self._pending_local = None
            self._pending_values.clear()
            self._notify()

    async def async_route(
        self,
        *,
        local_values: Mapping[Field, Any] | None,
        cloud_write: Callable[[MysaCloudCoordinator], Awaitable[None]] | None,
        cloud_applied: Callable[[DeviceState], bool] | None,
        intent: Mapping[str, Any] | None = None,
    ) -> None:
        """Serialize commands and remember those that supersede an in-flight write."""
        with self.history.command(self.history.command_id(), self.device_id):
            await self._async_route(
                local_values=local_values,
                cloud_write=cloud_write,
                cloud_applied=cloud_applied,
                intent=intent,
            )

    async def _async_route(
        self,
        *,
        local_values: Mapping[Field, Any] | None,
        cloud_write: Callable[[MysaCloudCoordinator], Awaitable[None]] | None,
        cloud_applied: Callable[[DeviceState], bool] | None,
        intent: Mapping[str, Any] | None,
    ) -> None:
        async with self._write_lock:
            if self._stopped:
                raise translated_error("thermostat_unreachable", name=self.name)
            intent_values = intent
            if intent_values is None and local_values is not None:
                intent_values = {
                    field.value: value for field, value in local_values.items()
                }
            try:
                await self._async_route_locked(
                    local_values=local_values,
                    cloud_write=cloud_write,
                    cloud_applied=cloud_applied,
                    intent=intent_values,
                )
            except HomeAssistantError as err:
                if isinstance(err.__cause__, MysaConnectionError) and not isinstance(
                    err.__cause__, MysaRateLimitError
                ):
                    # A lost response doesn't mean the newer write wasn't applied.
                    self._remember_intent(intent_values, cloud_write)
                raise
            except asyncio.CancelledError:
                if self._pending_local is not None:
                    self.history.record(
                        "command",
                        operation="local_write",
                        transport="homekit",
                        status="ambiguous",
                        error="cancelled",
                    )
                self._remember_intent(intent_values, cloud_write)
                raise
            else:
                self._remember_intent(intent_values, cloud_write)

    @callback
    def _remember_intent(
        self,
        values: Mapping[str, Any] | None,
        cloud_write: Callable[[MysaCloudCoordinator], Awaitable[None]] | None,
    ) -> None:
        """Keep later commands, including those whose cloud response was lost."""
        if self._pending_local is None or not values or cloud_write is None:
            return
        for key, value in values.items():
            self._reconcile.pop(key, None)
            self._reconcile_ids.pop(key, None)
            if key not in self._pending_values or value != self._pending_values[key]:
                # Reassert mode commands too: hardware can change more than one
                # characteristic. Reinserting keys preserves latest-command order.
                self._reconcile[key] = cloud_write
                self._reconcile_ids[key] = self.history.command_id()
        self._notify()

    async def _async_route_locked(
        self,
        *,
        local_values: Mapping[Field, Any] | None,
        cloud_write: Callable[[MysaCloudCoordinator], Awaitable[None]] | None,
        cloud_applied: Callable[[DeviceState], bool] | None,
        intent: Mapping[str, Any] | None,
    ) -> None:
        """Try HomeKit, then read back an uncertain result before cloud fallback."""
        outcome_unknown = False
        local = self.local
        if (
            local_values is not None
            and local is not None
            and self.local_ok
            and self._pending_local is None
            and all(local.supports(k) for k in local_values)
        ):
            self.history.record(
                "command",
                operation="local_write",
                transport="homekit",
                status="submitted",
                requested={k.value: v for k, v in local_values.items()},
                baseline={
                    k.value: {
                        "value": local.values.get(k),
                        "source": "homekit",
                        "observed_at": None,
                    }
                    for k in local_values
                },
            )
            started = time.monotonic()
            task = local.start_write(local_values)
            self._pending_local = task
            self._pending_values = {
                str(key): value for key, value in (intent or local_values).items()
            }
            task.add_done_callback(self._local_write_done)
            done, _ = await asyncio.wait({task}, timeout=LOCAL_WRITE_TIMEOUT)
            if task in done:
                self._pending_local = None
                self._pending_values.clear()
                error = task.exception()
                if error is None:
                    self.history.record(
                        "command",
                        operation="local_write",
                        transport="homekit",
                        status="accepted",
                        duration_ms=round((time.monotonic() - started) * 1000, 1),
                        evidence="none",
                    )
                    self._notify()
                    return
                self.history.record(
                    "command",
                    operation="local_write",
                    transport="homekit",
                    status="failed"
                    if isinstance(error, LocalWriteRejectedError)
                    else "ambiguous",
                    error=error_category(error),
                    duration_ms=round((time.monotonic() - started) * 1000, 1),
                )
                if isinstance(error, LocalWriteRejectedError):
                    raise translated_error(
                        "local_write_rejected", error="request_rejected"
                    )
                local.mark_unavailable(str(error) or type(error).__name__)
            else:
                outcome_unknown = True
                self._late_local = task
                self.history.record(
                    "command",
                    operation="local_write",
                    transport="homekit",
                    status="ambiguous",
                    error="connection_error",
                    duration_ms=round((time.monotonic() - started) * 1000, 1),
                )
                local.mark_unavailable("write timed out")
                # Read back before retrying anywhere.
                try:
                    values = await local.async_read(local_values)
                except LOCAL_ERRORS:
                    values = {}
                if values and all(values.get(k) == v for k, v in local_values.items()):
                    self.history.record(
                        "command",
                        operation="local_write",
                        transport="homekit",
                        status="confirmed",
                        evidence="device_report",
                        observations={
                            k.value: {
                                "value": v,
                                "source": "homekit",
                                "observed_at": None,
                                "fetched_at": dt_util.utcnow(),
                            }
                            for k, v in values.items()
                        },
                    )
                    local.mark_available()
                    self._notify()
                    return

        cloud = self.cloud
        if cloud_write is None or cloud is None:
            raise translated_error("thermostat_unreachable", name=self.name)

        if outcome_unknown and cloud_applied is not None:
            try:
                state = await cloud.async_fetch_state(self.device_id)
            except MysaError as err:
                LOGGER.debug(
                    "%s: couldn't verify the write in the cloud: %s",
                    self.history.label(self.device_id),
                    type(err).__name__,
                )
            else:
                if cloud_applied(state):
                    self.history.record(
                        "readback",
                        operation="local_write",
                        transport="cloud",
                        status="reported",
                        observations=state.observations,
                        evidence="none",
                    )
                    return
        self.history.record(
            "route",
            transport="cloud",
            status="fallback" if local_values is not None else "write",
        )
        await cloud_write(cloud)
