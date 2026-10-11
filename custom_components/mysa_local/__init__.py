"""Mysa Local: local-first Home Assistant integration for Mysa thermostats.

HomeKit on the LAN is the primary path. Mysa's cloud is optional: it adds the
settings HomeKit doesn't expose and takes over while the LAN path is down.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from functools import partial
import json

from homeassistant.const import (
    CONF_EMAIL,
    CONF_PASSWORD,
    CONF_SCAN_INTERVAL,
    Platform,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import (
    config_validation as cv,
    device_registry as dr,
    entity_registry as er,
    issue_registry as ir,
)
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.typing import ConfigType

from .account import async_delete_account_issues, async_update_account_issues
from .api import MysaAuth, MysaClient
from .cloud import MysaCloudCoordinator
from .const import (
    CONF_CLOUD_ENABLED,
    CONF_CLOUD_RUNTIME,
    CONF_DEVICE_ID,
    CONF_MIGRATE_FROM,
    CONF_PAIRING,
    CONF_REFRESH_TOKEN,
    CONF_USER_ID,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    ISSUES_URL,
    LOCAL_DOWN_ISSUE_AFTER,
    LOGGER,
    SUBENTRY_THERMOSTAT,
    USER_AGENT,
)
from .data import MysaConfigEntry, MysaRuntimeData
from .homekit import ENDPOINT_KEYS, async_get_controller, async_unpair
from .local import LOCAL_ERRORS, LocalThermostat
from .migration import async_migrate_homekit_controller
from .services import async_setup_services
from .thermostat import Thermostat

PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.CLIMATE,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.UPDATE,
]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

# Entities that only exist while the cloud is on.
CLOUD_ONLY_KEYS = frozenset(
    {
        "button_lock",
        "brightness_active",
        "brightness_idle",
        "setpoint_min",
        "setpoint_max",
        "temperature_offset",
        "heater_type",
        "firmware",
        "wifi_signal",
        "humidity",
        "cloud_online",
        "schedule_hold",
        "mysa_account",
    }
)
LOCAL_START_WAIT = 8.0


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the integration's actions."""
    async_setup_services(hass)
    return True


def _fingerprint(entry: MysaConfigEntry) -> object:
    """What a reload depends on (ignores IP updates and token refreshes)."""
    subentries = {}
    for subentry_id, subentry in entry.subentries.items():
        data = dict(subentry.data)
        pairing = dict(data.get(CONF_PAIRING) or {})
        for key in ENDPOINT_KEYS:
            pairing.pop(key, None)
        data[CONF_PAIRING] = pairing
        data.pop(CONF_MIGRATE_FROM, None)
        subentries[subentry_id] = (
            subentry.title,
            json.dumps(data, sort_keys=True, default=str),
        )
    return (
        json.dumps(dict(entry.options), sort_keys=True, default=str),
        bool(entry.data.get(CONF_REFRESH_TOKEN)),
        entry.data.get(CONF_USER_ID),
        subentries,
    )


async def async_setup_entry(hass: HomeAssistant, entry: MysaConfigEntry) -> bool:
    """Connect to every thermostat and, if enabled, to Mysa's cloud."""
    subentries = [
        s for s in entry.subentries.values() if s.subentry_type == SUBENTRY_THERMOSTAT
    ]
    token = entry.data.get(CONF_REFRESH_TOKEN)
    cloud_on = bool(token) and entry.options.get(CONF_CLOUD_ENABLED, True)

    client: MysaClient | None = None
    cloud: MysaCloudCoordinator | None = None
    if cloud_on:

        @callback
        def _save_token(new_token: str) -> None:
            hass.config_entries.async_update_entry(
                entry, data={**entry.data, CONF_REFRESH_TOKEN: new_token}
            )

        session = async_get_clientsession(hass)
        auth = MysaAuth(
            session,
            token,
            user_agent=USER_AGENT,
            on_refresh_token=_save_token,
            email=entry.data.get(CONF_EMAIL),
            password=entry.data.get(CONF_PASSWORD),
        )
        client = MysaClient(session, auth, user_agent=USER_AGENT)
        cloud = MysaCloudCoordinator(
            hass,
            entry,
            client,
            device_ids=[s.data[CONF_DEVICE_ID] for s in subentries],
            scan_interval=entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
            usage_device_ids=[
                s.data[CONF_DEVICE_ID]
                for s in subentries
                if s.data.get(CONF_CLOUD_RUNTIME, True)
            ],
        )

    runtime = MysaRuntimeData(
        client=client, cloud=cloud, fingerprint=_fingerprint(entry)
    )
    entry.runtime_data = runtime

    for subentry in subentries:
        if subentry.data.get(CONF_MIGRATE_FROM):
            await async_migrate_homekit_controller(hass, entry, subentry)

    controller = None
    if any(s.data.get(CONF_PAIRING) for s in subentries):
        controller = await async_get_controller(hass)
    for subentry in entry.subentries.values():
        if subentry.subentry_type != SUBENTRY_THERMOSTAT:
            continue
        local = None
        if controller is not None and (pairing := subentry.data.get(CONF_PAIRING)):
            local = LocalThermostat(hass, controller, pairing, subentry.title)
        runtime.thermostats[subentry.subentry_id] = Thermostat(
            hass, subentry, local, lambda: runtime.cloud
        )

    # Give the LAN connections a few seconds; slow ones keep connecting in the
    # background and their entities turn available when they're up.
    starts = [
        entry.async_create_background_task(
            hass, t.local.async_start(), f"mysa {t.name} local start"
        )
        for t in runtime.thermostats.values()
        if t.local is not None
    ]
    if starts:
        await asyncio.wait(starts, timeout=LOCAL_START_WAIT)

    if cloud is not None:
        await cloud.async_refresh()

        @callback
        def _cloud_updated() -> None:
            for thermostat in runtime.thermostats.values():
                thermostat.notify()
            async_update_account_issues(hass, entry)
            _async_update_devices(hass, entry)

        entry.async_on_unload(cloud.async_add_listener(_cloud_updated))
        # A thermostat that is unreachable from the start goes into cloud
        # fallback (live mode, faster polling) now, not at the next poll.
        _cloud_updated()
    else:
        _async_remove_cloud_entities(hass, entry)
        async_update_account_issues(hass, entry)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    entry.async_on_unload(
        async_track_time_interval(
            hass,
            partial(_async_check_local_health, hass, entry),
            timedelta(minutes=1),
            name=f"{DOMAIN} local health",
        )
    )
    return True


@callback
def _async_update_devices(hass: HomeAssistant, entry: MysaConfigEntry) -> None:
    """Keep each device's model line, firmware and serial up to date.

    Entities only set the device info when they're added, and leave out what
    isn't known then. This fills in a firmware update, a serial that becomes
    known once HomeKit connects, and the Mysa app name on the model line.
    """
    dev_reg = dr.async_get(hass)
    for thermostat in entry.runtime_data.thermostats.values():
        device = dev_reg.async_get_device_by_identifier(
            (DOMAIN, thermostat.device_id), entry.entry_id
        )
        if device is None:
            continue
        changes: dict[str, str] = {}
        model = thermostat.device_model
        if model and device.model != model:
            changes["model"] = model
        firmware = thermostat.firmware
        stored = device.sw_version or ""
        # HomeKit has only three parts of the version ("3.17.5" for the cloud's
        # "3.17.5.13"): don't swap the full one for the short form of the same.
        if firmware and firmware != stored and not stored.startswith(f"{firmware}."):
            changes["sw_version"] = firmware
        serial = thermostat.serial
        if serial and device.serial_number != serial:
            changes["serial_number"] = serial
        if changes:
            dev_reg.async_update_device(device.id, **changes)  # type: ignore[arg-type]


@callback
def _async_remove_cloud_entities(hass: HomeAssistant, entry: MysaConfigEntry) -> None:
    """With the cloud off, drop the entities only the cloud can feed."""
    ent_reg = er.async_get(hass)
    for reg_entry in er.async_entries_for_config_entry(ent_reg, entry.entry_id):
        key = reg_entry.unique_id.split("_", 1)[-1]
        if key in CLOUD_ONLY_KEYS:
            ent_reg.async_remove(reg_entry.entity_id)


@callback
def _async_check_local_health(
    hass: HomeAssistant, entry: MysaConfigEntry, _now: datetime | None = None
) -> None:
    """Raise a repair issue when a thermostat's LAN path stays down."""
    for thermostat in entry.runtime_data.thermostats.values():
        issue_id = f"local_unreachable_{thermostat.device_id}"
        down = thermostat.local.down_for() if thermostat.local else None
        if down is not None and down >= LOCAL_DOWN_ISSUE_AFTER:
            assert thermostat.local is not None
            ir.async_create_issue(
                hass,
                DOMAIN,
                issue_id,
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key=(
                    "local_unreachable_cloud"
                    if thermostat.cloud_ok
                    else "local_unreachable"
                ),
                translation_placeholders={
                    "name": thermostat.name,
                    "host": thermostat.local.host or "unknown",
                    "minutes": str(int(down.total_seconds() // 60)),
                },
                learn_more_url=f"{ISSUES_URL}",
            )
        else:
            ir.async_delete_issue(hass, DOMAIN, issue_id)
    # Without the cloud this is the only regular update of the device info.
    _async_update_devices(hass, entry)


async def _async_update_listener(hass: HomeAssistant, entry: MysaConfigEntry) -> None:
    """Reload when settings or the thermostat list change; unpair removed ones."""
    runtime = entry.runtime_data
    if _fingerprint(entry) == runtime.fingerprint:
        return
    for subentry_id, thermostat in runtime.thermostats.items():
        if subentry_id in entry.subentries:
            continue
        ir.async_delete_issue(hass, DOMAIN, f"local_unreachable_{thermostat.device_id}")
        async_delete_account_issues(hass, thermostat.device_id)
        if thermostat.local is None:
            continue
        LOGGER.info("Removing the HomeKit pairing of %s", thermostat.name)
        try:
            async with asyncio.timeout(15):
                await thermostat.local.pairing.remove_pairing(
                    thermostat.local.pairing_data["iOSPairingId"]
                )
        except LOCAL_ERRORS as err:
            LOGGER.warning(
                "Couldn't unpair %s (%s); reset its HomeKit pairing on the device "
                "before pairing it again",
                thermostat.name,
                err,
            )
    # The reload reads the latest data and options, so one is enough even when
    # an options flow changes data and options back to back.
    runtime.fingerprint = _fingerprint(entry)
    hass.config_entries.async_schedule_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: MysaConfigEntry) -> bool:
    """Disconnect everything."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await asyncio.gather(
            *(
                t.local.async_stop()
                for t in entry.runtime_data.thermostats.values()
                if t.local is not None
            )
        )
    return unloaded


async def async_remove_entry(hass: HomeAssistant, entry: MysaConfigEntry) -> None:
    """Unpair every thermostat so it can be paired again elsewhere."""
    for subentry in entry.subentries.values():
        if pairing := subentry.data.get(CONF_PAIRING):
            await async_unpair(hass, pairing)
        ir.async_delete_issue(
            hass, DOMAIN, f"local_unreachable_{subentry.data.get(CONF_DEVICE_ID)}"
        )
        async_delete_account_issues(hass, str(subentry.data.get(CONF_DEVICE_ID)))
