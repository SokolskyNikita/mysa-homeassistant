"""Fixes for the account repair issues: add to the account, or pair locally."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from homeassistant.components.repairs import RepairsFlow
from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult

from .account import ISSUE_CLOUD_ONLY, account_client, async_refresh_account
from .const import CONF_DEVICE_ID, CONF_MODEL, CONF_PAIRING
from .flows.thermostat import ThermostatSteps
from .protocols.cloud import MysaClient
from .protocols.homekit import AccessoryInfo


class _ThermostatRepairFlow(ThermostatSteps, RepairsFlow):
    """A fix for one thermostat of the Mysa Local entry."""

    def __init__(self, data: Mapping[str, Any]) -> None:
        """Remember which thermostat the issue is about."""
        super().__init__()
        self._data = dict(data)

    def _entry(self) -> ConfigEntry | None:
        return self.hass.config_entries.async_get_entry(str(self._data["entry_id"]))

    def _subentry(self) -> ConfigSubentry | None:
        entry = self._entry()
        if entry is None:
            return None
        return entry.subentries.get(str(self._data["subentry_id"]))

    def _account_client(self) -> MysaClient | None:
        return account_client(self._entry())


class AccountRepairFlow(_ThermostatRepairFlow):
    """Put a thermostat (back) on the Mysa account."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Go straight to the account step."""
        subentry = self._subentry()
        if subentry is None or self._account_client() is None:
            return self.async_abort(reason="not_available")
        self._thermostat.account_device = (
            subentry.data[CONF_DEVICE_ID],
            subentry.data.get(CONF_MODEL),
            subentry.title,
        )
        self._thermostat.account_optional = False
        return await self.async_step_account()  # type: ignore[no-any-return]

    async def _async_account_done(self) -> FlowResult:
        if (entry := self._entry()) is not None:
            await async_refresh_account(entry)
        return self.async_create_entry(data={})


class LocalPairRepairFlow(_ThermostatRepairFlow):
    """Pair a cloud-only thermostat over HomeKit."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Look for the thermostat on the network."""
        subentry = self._subentry()
        if subentry is None:
            return self.async_abort(reason="not_available")
        self._thermostat.target_device_id = subentry.data[CONF_DEVICE_ID]
        self._thermostat.target_name = subentry.title
        return await self.async_step_local_find()  # type: ignore[no-any-return]

    async def _async_thermostat_ready(
        self,
        info: AccessoryInfo,
        pairing: Mapping[str, Any],
        migrate_from: str | None = None,
    ) -> FlowResult:
        if (wrong := await self._async_wrong_thermostat(info, pairing)) is not None:
            return wrong  # type: ignore[no-any-return]
        entry = self._entry()
        subentry = self._subentry()
        if entry is None or subentry is None:
            return self.async_abort(reason="not_available")
        # The entry reloads with the new pairing and the issue clears itself.
        self.hass.config_entries.async_update_subentry(
            entry,
            subentry,
            data={
                **subentry.data,
                CONF_PAIRING: dict(pairing),
                CONF_MODEL: info.model or subentry.data.get(CONF_MODEL),
            },
        )
        return self.async_create_entry(data={})

    async def _async_local_skipped(self) -> FlowResult:
        return self.async_abort(reason="not_now")


async def async_create_fix_flow(
    hass: HomeAssistant,
    issue_id: str,
    data: dict[str, str | int | float | None] | None,
) -> RepairsFlow:
    """The fix for an account issue."""
    assert data is not None
    if issue_id.startswith(f"{ISSUE_CLOUD_ONLY}_"):
        return LocalPairRepairFlow(data)
    return AccountRepairFlow(data)
