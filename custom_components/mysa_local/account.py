"""Linking thermostats between Home Assistant and the Mysa account.

A thermostat can be paired locally (HomeKit), be on the Mysa account (cloud),
or both. This module says which, puts thermostats on the account or takes them
off, and keeps a repair issue open while the two don't match.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Final

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir
from mysa_client.cloud import WIFI_ONLY_FAMILIES, MysaClient, MysaError, model_family

from .const import DOMAIN, LOGGER

if TYPE_CHECKING:
    from .cloud_coordinator import MysaCloudCoordinator
    from .data import MysaConfigEntry, MysaRuntimeData

ACCOUNT_ON: Final = "on_account"
ACCOUNT_OFF: Final = "not_on_account"

# Repair issue ID prefixes; the thermostat's device ID follows.
ISSUE_NOT_ON_ACCOUNT: Final = "not_on_account"
ISSUE_CLOUD_ONLY: Final = "cloud_only"

_MAC_ID = re.compile(r"[0-9a-f]{12}")


def can_claim(device_id: str, model: str | None) -> bool:
    """True if this thermostat can be put on an account from Home Assistant.

    That needs its real Mysa ID (the Wi-Fi MAC) and a model that joins with
    Wi-Fi details alone; newer models also need Bluetooth credentials.
    """
    return bool(_MAC_ID.fullmatch(device_id)) and (
        model_family(model) in WIFI_ONLY_FAMILIES
    )


def can_pair_locally(model: str | None) -> bool:
    """True for the models that have HomeKit (the newer ones use Matter)."""
    return model_family(model) in WIFI_ONLY_FAMILIES


def can_import_cloud(model: str | None) -> bool:
    """Already enrolled devices can be imported, independently of provisioning."""
    return True


def is_supported_model(model: str | None) -> bool:
    """Local discovery/migration policy; cloud import is independent.

    Newer models lack an implemented local protocol. Unknown models still pass
    discovery with the untested-model notice, preserving the existing policy.
    """
    family = model_family(model)
    return family is None or family in WIFI_ONLY_FAMILIES


def cloud_account_status(
    cloud: MysaCloudCoordinator | None, device_id: str
) -> str | None:
    """on_account, not_on_account, or None while unknown (cloud off or failing)."""
    if cloud is None or cloud.data is None or not cloud.last_update_success:
        return None
    return ACCOUNT_ON if device_id in cloud.data.records else ACCOUNT_OFF


def account_status(entry: MysaConfigEntry, device_id: str) -> str | None:
    """Account status of a thermostat, from a loaded entry."""
    runtime = getattr(entry, "runtime_data", None)
    return cloud_account_status(runtime.cloud if runtime else None, device_id)


def account_client(entry: MysaConfigEntry | None) -> MysaClient | None:
    """The entry's cloud client, if the cloud is on and loaded."""
    runtime: MysaRuntimeData | None = getattr(entry, "runtime_data", None)
    if runtime is None or runtime.cloud is None:
        return None
    return runtime.client


async def async_add_to_account(
    hass: HomeAssistant,
    client: MysaClient,
    *,
    device_id: str,
    model: str | None,
    name: str,
    home_id: str,
) -> bool:
    """Put a thermostat on the Mysa account. False if it was already there.

    Sends Mysa's older, smaller claim (ID, name, model, home and Home
    Assistant's time zone). A thermostat that was on the account before keeps
    its stored settings (heater type, display units...); the 4.20.1 app's body
    would reset them to its defaults (seen 2026-10-11: Fan Forced became
    Baseboard).
    """
    return await client.add_device(
        device_id,
        home_id=home_id,
        model=model or "",
        time_zone=str(hass.config.time_zone),
        name=name,
        app_defaults=False,
    )


async def async_remove_from_account(
    client: MysaClient, device_id: str, home_id: str | None
) -> None:
    """Take a thermostat off the Mysa account, cleaning up like Mysa's app.

    The app first deletes its schedule and takes it out of the home's shortcuts.
    That cleanup is best effort here; the removal itself must succeed. Mysa
    keeps the thermostat's settings, and adding it back restores them.
    """
    if home_id:
        try:
            for schedule in await client.get_all_schedules(home_id):
                if device_id in (schedule.get("deviceIds") or []):
                    await client.remove_schedule(schedule, device_id)
            for shortcut in await client.get_shortcuts(home_id):
                actions = shortcut.get("DeviceActions") or {}
                if isinstance(actions, dict) and device_id in actions:
                    await client.update_shortcut(
                        str(shortcut.get("Id")),
                        {k: v for k, v in actions.items() if k != device_id},
                        all_devices=bool(shortcut.get("AllDevices")),
                    )
        except MysaError as err:
            LOGGER.warning(
                "Couldn't clean up the schedule and shortcuts of %s before "
                "removing it from the Mysa account: %s",
                "[redacted]",
                type(err).__name__,
            )
    await client.delete_device(device_id)


async def async_refresh_account(entry: MysaConfigEntry) -> None:
    """Re-read the account's thermostat list now (after adding or removing)."""
    runtime = getattr(entry, "runtime_data", None)
    cloud = runtime.cloud if runtime else None
    if cloud is not None:
        cloud.mark_stale("records")
        await cloud.async_refresh()


@callback
def async_delete_account_issues(hass: HomeAssistant, device_id: str) -> None:
    """Drop the account repair issues of one thermostat."""
    for prefix in (ISSUE_NOT_ON_ACCOUNT, ISSUE_CLOUD_ONLY):
        ir.async_delete_issue(hass, DOMAIN, f"{prefix}_{device_id}")


@callback
def async_update_account_issues(hass: HomeAssistant, entry: MysaConfigEntry) -> None:
    """Warn about thermostats whose local and cloud setup don't match.

    - Paired locally but not on the Mysa account: the cloud extras and the
      cloud fallback can't work. Fix: add it to the account.
    - Added from the account (cloud only) but no longer on it: it can't be
      reached at all. Fix: add it back.
    - Cloud only, on the account, with HomeKit available: it works, but
      depends on Mysa's servers. Fix: pair it locally.
    Every issue can be ignored; nothing is raised while the cloud is off or
    its status is unknown.
    """
    runtime = entry.runtime_data
    for thermostat in runtime.thermostats.values():
        device_id = thermostat.device_id
        if runtime.cloud is None:
            async_delete_account_issues(hass, device_id)
            continue
        status = cloud_account_status(runtime.cloud, device_id)
        if status is None:
            continue
        data: dict[str, str | int | float | None] = {
            "entry_id": entry.entry_id,
            "subentry_id": thermostat.subentry_id,
            "device_id": device_id,
        }
        placeholders = {"name": thermostat.name}
        model = thermostat.model
        account_issue = f"{ISSUE_NOT_ON_ACCOUNT}_{device_id}"
        local_issue = f"{ISSUE_CLOUD_ONLY}_{device_id}"
        if status == ACCOUNT_OFF:
            fixable = can_claim(device_id, model)
            key = "not_on_account" if thermostat.has_local else "removed_from_account"
            ir.async_create_issue(
                hass,
                DOMAIN,
                account_issue,
                is_fixable=fixable,
                severity=ir.IssueSeverity.WARNING,
                # A translation has either a fix flow or a plain description.
                translation_key=key if fixable else f"{key}_info",
                translation_placeholders=placeholders,
                data=data,
            )
        else:
            ir.async_delete_issue(hass, DOMAIN, account_issue)
        if (
            status == ACCOUNT_ON
            and not thermostat.has_local
            and can_pair_locally(model)
        ):
            ir.async_create_issue(
                hass,
                DOMAIN,
                local_issue,
                is_fixable=True,
                severity=ir.IssueSeverity.WARNING,
                translation_key="cloud_only",
                translation_placeholders=placeholders,
                data=data,
            )
        else:
            ir.async_delete_issue(hass, DOMAIN, local_issue)
