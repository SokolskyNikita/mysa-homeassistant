"""Firmware update (check always; install only when the user allows it)."""

from __future__ import annotations

import asyncio
from typing import Any

from homeassistant.components.update import (
    UpdateDeviceClass,
    UpdateEntity,
    UpdateEntityFeature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .cloud_coordinator import translated_error
from .const import CONF_FIRMWARE_INSTALL, LOGGER
from .data import MysaConfigEntry
from .entity import MysaCloudEntity, async_add_when_ready
from .protocols.cloud import MysaError
from .thermostat import Thermostat

PARALLEL_UPDATES = 1
INSTALL_POLL = 20
INSTALL_TIMEOUT = 6 * 60


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MysaConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the firmware entity once the cloud has checked for updates."""
    allow_install = bool(entry.options.get(CONF_FIRMWARE_INSTALL, False))
    async_add_when_ready(
        entry,
        async_add_entities,
        lambda t: (
            [FirmwareUpdate(t, allow_install)]
            if t.firmware_info and not t.basic_cloud_only
            else []
        ),
    )


class FirmwareUpdate(MysaCloudEntity, UpdateEntity):
    """Mysa firmware, as offered by Mysa's cloud."""

    _attr_device_class = UpdateDeviceClass.FIRMWARE
    _attr_translation_key = "firmware"

    def __init__(self, thermostat: Thermostat, allow_install: bool) -> None:
        """Create the entity. Install stays off unless the user enabled it."""
        super().__init__(thermostat, "firmware")
        self._attr_supported_features = (
            UpdateEntityFeature.INSTALL if allow_install else UpdateEntityFeature(0)
        )
        self._installing = False

    @property
    def available(self) -> bool:
        """Available while the cloud answers."""
        cloud = self.thermostat.cloud
        return cloud is not None and cloud.last_update_success

    @property
    def installed_version(self) -> str | None:
        """Firmware on the thermostat."""
        info = self.thermostat.firmware_info
        return (info.installed_version if info else None) or self.thermostat.firmware

    @property
    def latest_version(self) -> str | None:
        """Firmware Mysa offers for it."""
        info = self.thermostat.firmware_info
        if info is None:
            return None
        if info.update_available and info.allowed_version:
            return info.allowed_version
        return self.installed_version

    @property
    def in_progress(self) -> bool:
        """True while an install we started is running."""
        return self._installing

    async def async_install(
        self, version: str | None, backup: bool, **kwargs: Any
    ) -> None:
        """Ask Mysa to push the update, then wait for the thermostat to finish."""
        cloud = self.thermostat.require_cloud()
        device_id = self.thermostat.device_id
        LOGGER.warning(
            "Starting a firmware update on %s at the user's request",
            self.thermostat.history.label(self.thermostat.device_id),
        )
        await cloud.async_call(
            lambda: cloud.client.start_firmware_update(device_id),
            stale=[f"firmware:{device_id}"],
            device_id=self.thermostat.device_id,
            operation="firmware_update",
        )
        self._installing = True
        self.async_write_ha_state()
        try:
            async with asyncio.timeout(INSTALL_TIMEOUT):
                while True:
                    await asyncio.sleep(INSTALL_POLL)
                    try:
                        info = await cloud.client.get_firmware_info(device_id)
                    except MysaError as err:
                        LOGGER.debug(
                            "Firmware status check failed: %s", type(err).__name__
                        )
                        continue
                    if not info.update_available:
                        break
        except TimeoutError as err:
            raise translated_error(
                "firmware_timeout", name=self.thermostat.name
            ) from err
        finally:
            self._installing = False
            cloud.mark_stale(f"firmware:{device_id}")
            await cloud.async_request_refresh()
            self.async_write_ha_state()
