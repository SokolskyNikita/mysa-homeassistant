"""Serialize thermostat commands and reconcile delayed local writes.

The router chooses the transport; protocol clients own encoding and I/O. A local
write that outlives its timeout stays alive until the request settles, so the
HomeKit session remains usable and newer cloud commands can be reasserted.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError

from .cloud_coordinator import MysaCloudCoordinator, translated_error
from .const import LOCAL_WRITE_TIMEOUT, LOGGER
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
    ) -> None:
        """Attach existing clients without starting any network activity."""
        self.hass = hass
        self.device_id = device_id
        self.name = name
        self.local = local
        self._cloud_getter = cloud_getter
        self._notify = notify
        self._write_lock = asyncio.Lock()
        self._pending_local: asyncio.Task[None] | None = None
        self._pending_values: dict[str, Any] = {}
        self._reconcile: dict[
            str, Callable[[MysaCloudCoordinator], Awaitable[None]]
        ] = {}
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
            task.exception()
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
                        await write(cloud)
                        for key, pending in list(self._reconcile.items()):
                            if pending is write:
                                del self._reconcile[key]
                except HomeAssistantError as err:
                    # Keep the desired commands and retry on the next update.
                    LOGGER.warning(
                        "%s: couldn't reconcile a late write: %s", self.name, err
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
            if key not in self._pending_values or value != self._pending_values[key]:
                # Reassert mode commands too: hardware can change more than one
                # characteristic. Reinserting keys preserves latest-command order.
                self._reconcile[key] = cloud_write
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
                    self._notify()
                    return
                if isinstance(error, LocalWriteRejectedError):
                    raise translated_error("local_write_rejected", error=str(error))
                local.mark_unavailable(str(error) or type(error).__name__)
            else:
                outcome_unknown = True
                local.mark_unavailable("write timed out")
                # Read back before retrying anywhere.
                try:
                    values = await local.async_read(local_values)
                except LOCAL_ERRORS:
                    values = {}
                if values and all(values.get(k) == v for k, v in local_values.items()):
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
                    "%s: couldn't verify the write in the cloud: %s", self.name, err
                )
            else:
                if cloud_applied(state):
                    LOGGER.debug("%s: the timed-out local write had landed", self.name)
                    return
        await cloud_write(cloud)
