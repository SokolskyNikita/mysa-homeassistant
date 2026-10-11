"""Base entity classes."""

from __future__ import annotations

from collections.abc import Callable, Iterable

from homeassistant.core import callback
from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC, DeviceInfo
from homeassistant.helpers.entity import Entity, EntityDescription
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DOMAIN, MANUFACTURER
from .data import MysaConfigEntry
from .protocols.cloud import DeviceState
from .thermostat import Thermostat


class MysaEntity(Entity):
    """An entity of one Mysa thermostat."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self,
        thermostat: Thermostat,
        key: str,
        description: EntityDescription | None = None,
    ) -> None:
        """Attach the entity to its thermostat's device."""
        self.thermostat = thermostat
        if description is not None:
            self.entity_description = description
        self._attr_unique_id = f"{thermostat.device_id}_{key}"
        device_info = DeviceInfo(
            identifiers={(DOMAIN, thermostat.device_id)},
            connections={(CONNECTION_NETWORK_MAC, thermostat.mac)},
            manufacturer=MANUFACTURER,
            model=thermostat.device_model,
            name=thermostat.name,
        )
        # Leave out what isn't known yet (no LAN connection or cloud at
        # startup): an explicit None would clear the stored value.
        if model_id := thermostat.model:
            device_info["model_id"] = model_id
        if serial := thermostat.serial:
            device_info["serial_number"] = serial
        if firmware := thermostat.firmware:
            device_info["sw_version"] = firmware
        self._attr_device_info = device_info

    @property
    def available(self) -> bool:
        """Available while either the LAN or the cloud reaches the thermostat."""
        return self.thermostat.available

    async def async_added_to_hass(self) -> None:
        """Follow the thermostat's updates."""
        await super().async_added_to_hass()
        self.async_on_remove(self.thermostat.add_listener(self._handle_update))

    @callback
    def _handle_update(self) -> None:
        self.async_write_ha_state()


class MysaCloudEntity(MysaEntity):
    """An entity that only the cloud can feed."""

    @property
    def available(self) -> bool:
        """Available while the cloud poll succeeds and sees the thermostat online."""
        return self.thermostat.cloud_connected

    @property
    def cloud_state(self) -> DeviceState | None:
        """Latest cloud state."""
        return self.thermostat.cloud_state


@callback
def async_add_when_ready(
    entry: MysaConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
    factory: Callable[[Thermostat], Iterable[MysaEntity]],
) -> None:
    """Add entities now, and again whenever new cloud data makes more possible.

    Cloud entities depend on each model's capability data, which may arrive
    after setup (for example when the cloud was briefly down).
    """
    added: set[str] = set()

    @callback
    def _add() -> None:
        for thermostat in entry.runtime_data.thermostats.values():
            new = [
                entity
                for entity in factory(thermostat)
                if entity.unique_id not in added
            ]
            if new:
                added.update(e.unique_id for e in new if e.unique_id)
                async_add_entities(new, config_subentry_id=thermostat.subentry_id)

    _add()
    if (cloud := entry.runtime_data.cloud) is not None:
        entry.async_on_unload(cloud.async_add_listener(_add))
