"""Base entity classes."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Iterable
from contextlib import asynccontextmanager

from homeassistant.core import callback
from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC, DeviceInfo
from homeassistant.helpers.entity import Entity, EntityDescription
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from mysa_client.cloud import DeviceState

from .const import DOMAIN, MANUFACTURER
from .data import MysaConfigEntry
from .temperature import Unit, user_unit
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
        self._service_calls = 0
        self._removed = False

    @property
    def owner_unit(self) -> Unit:
        """The owner's temperature unit (C or F)."""
        return user_unit(self.hass)

    @property
    def available(self) -> bool:
        """Available while either the LAN or the cloud reaches the thermostat."""
        return self.thermostat.available

    async def async_added_to_hass(self) -> None:
        """Follow the thermostat's updates."""
        await super().async_added_to_hass()
        self.async_on_remove(self.thermostat.add_listener(self._handle_update))

    async def async_will_remove_from_hass(self) -> None:
        """Stop deferred state writes."""
        self._removed = True
        await super().async_will_remove_from_hass()

    @callback
    def _handle_update(self) -> None:
        self.async_write_ha_state()

    @asynccontextmanager
    async def _service_call(self) -> AsyncIterator[None]:
        """Write this entity's state once more after its service call returns.

        State is still written during the call, so scripts see the new value
        as soon as the call returns. The HA frontend ignores updates that
        arrive while its own call is in flight, so the extra write afterwards
        is what reaches it when the state changed only then. If the state was
        already written during the call unchanged, the state machine drops the
        repeat; a clamped value can then leave the card showing its own value
        until the next update (owner, 2026-11-15).
        """
        self._service_calls += 1
        try:
            yield
        finally:
            self._service_calls -= 1
            if not self._service_calls:
                self._schedule_write_after_call()

    @callback
    def _schedule_write_after_call(self) -> None:
        # Two loop turns: one for the service task that ran this method to
        # finish, one for its caller (the websocket handler) to send the result.
        loop = self.hass.loop
        loop.call_soon(loop.call_soon, self._write_after_call)

    @callback
    def _write_after_call(self) -> None:
        if not self._removed and not self._service_calls:
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
    """Add entities at setup and after local or cloud capability discovery."""
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

    for thermostat in entry.runtime_data.thermostats.values():
        if thermostat.local is not None:
            entry.async_on_unload(thermostat.local.add_capability_listener(_add))
    _add()
    if (cloud := entry.runtime_data.cloud) is not None:
        entry.async_on_unload(cloud.async_add_listener(_add))
    if (mqtt := entry.runtime_data.mqtt) is not None:
        entry.async_on_unload(mqtt.async_add_listener(_add))
