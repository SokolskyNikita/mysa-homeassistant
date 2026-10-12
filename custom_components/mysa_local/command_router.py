"""Home Assistant adapter for the reusable Mysa command router."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from mysa_client.cloud import (
    DeviceState,
    MysaConnectionError,
    MysaError,
    MysaRateLimitError,
)
from mysa_client.homekit import Field
from mysa_client.runtime.routing import (
    CommandRouter as RuntimeCommandRouter,
    RouteOutcome,
    RouteRequest,
)

from .cloud_coordinator import MysaCloudCoordinator, translated_error
from .const import LOCAL_WRITE_TIMEOUT, LOGGER
from .event_history import EventHistory, error_category
from .local_adapter import LOCAL_ERRORS, LocalThermostat, LocalWriteRejectedError


class CommandRouter:
    """Adapt runtime routing outcomes and task lifecycle to Home Assistant."""

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
        """Wire existing clients without starting network activity."""
        self.history = history if history is not None else EventHistory()
        self.hass = hass
        self.device_id = device_id
        self.name = name
        self.local = local
        self._cloud_getter = cloud_getter
        self._notify = notify
        self._reconcile_task: asyncio.Task[None] | None = None
        self._stopped = False
        self._router: RuntimeCommandRouter[
            Field, Any, MysaCloudCoordinator, DeviceState
        ] = RuntimeCommandRouter(
            device_id=device_id,
            name=name,
            local=local,
            cloud_getter=cloud_getter,
            cloud_read=lambda cloud: cloud.async_fetch_state(device_id),
            history=self.history,
            on_change=notify,
            on_reconcile_ready=self.schedule_reconciliation,
            on_reconcile_error=self._log_reconcile_error,
            write_timeout=LOCAL_WRITE_TIMEOUT,
            is_local_error=lambda error: isinstance(error, LOCAL_ERRORS),
            is_local_rejection=lambda error: isinstance(error, LocalWriteRejectedError),
            is_cloud_confirmation_error=lambda error: isinstance(error, MysaError),
            is_ambiguous_cloud_error=self._is_ambiguous_cloud_error,
            is_reconcile_error=lambda error: isinstance(error, HomeAssistantError),
            error_category=error_category,
        )

    @property
    def cloud(self) -> MysaCloudCoordinator | None:
        """The current coordinator, including when cloud access is disabled."""
        return self._cloud_getter()

    @property
    def local_ok(self) -> bool:
        """Whether local state is trustworthy after pending reconciliation."""
        return self._router.local_ok

    @callback
    def stop(self) -> None:
        """Stop scheduling reconciliation when this thermostat unloads."""
        self._stopped = True
        self._router.stop()
        if self._reconcile_task is not None:
            self._reconcile_task.cancel()

    async def async_stop(self) -> None:
        """Close HomeKit, drain its outstanding write, then reconcile intents."""
        self.stop()
        if self._reconcile_task is not None:
            await asyncio.gather(self._reconcile_task, return_exceptions=True)
        await self._router.async_stop()

    @callback
    def schedule_reconciliation(self) -> None:
        """Schedule the framework-owned task once a delayed write has settled."""
        if (
            self._stopped
            or self.hass.is_stopping
            or self._router.pending_local is None
            or not self._router.pending_local.done()
            or (self._reconcile_task is not None and not self._reconcile_task.done())
        ):
            return
        self._reconcile_task = self.hass.async_create_background_task(
            self._async_reconcile(), f"mysa {self.name} reconcile local write"
        )

    async def _async_reconcile(self, *, allow_stopped: bool = False) -> None:
        """Run the reusable reconciliation state machine."""
        await self._router.async_reconcile(allow_stopped=allow_stopped)

    async def async_route(
        self,
        *,
        local_values: Mapping[Field, Any] | None,
        cloud_write: Callable[[MysaCloudCoordinator], Awaitable[None]] | None,
        cloud_applied: Callable[[DeviceState], bool] | None,
        intent: Mapping[str, Any] | None = None,
    ) -> None:
        """Route an already prepared write while preserving the HA API."""
        result = await self._router.async_route(
            local_values=local_values,
            cloud_write=cloud_write,
            cloud_applied=cloud_applied,
            intent=intent,
        )
        self._raise_for_result(result.outcome)

    async def async_route_prepared(
        self,
        prepare: Callable[
            [],
            RouteRequest[Field, Any, MysaCloudCoordinator, DeviceState]
            | Awaitable[RouteRequest[Field, Any, MysaCloudCoordinator, DeviceState]],
        ],
    ) -> None:
        """Prepare state-dependent commands only after acquiring the write lock."""
        result = await self._router.async_route_prepared(prepare)
        self._raise_for_result(result.outcome)

    def _raise_for_result(self, outcome: RouteOutcome) -> None:
        if outcome == RouteOutcome.LOCAL_REJECTED:
            raise translated_error("local_write_rejected", error="request_rejected")
        if outcome in {RouteOutcome.UNAVAILABLE, RouteOutcome.STOPPED}:
            raise translated_error("thermostat_unreachable", name=self.name)

    def _log_reconcile_error(self, error: BaseException) -> None:
        LOGGER.warning(
            "%s: couldn't reconcile a late write: %s",
            self.history.label(self.device_id),
            type(error).__name__,
        )

    @staticmethod
    def _is_ambiguous_cloud_error(error: BaseException) -> bool:
        cause = error.__cause__
        return isinstance(cause, MysaConnectionError) and not isinstance(
            cause, MysaRateLimitError
        )
