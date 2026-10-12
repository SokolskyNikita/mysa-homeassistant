"""Mysa-specific HomeKit characteristic parsing and operations."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
import logging
from time import monotonic
from types import MappingProxyType
from typing import Any

from aiohomekit.exceptions import (
    AccessoryDisconnectedError,
    AccessoryNotFoundError,
    AuthenticationError,
    EncryptionError,
    HomeKitException,
)
from aiohomekit.model.characteristics import CharacteristicsTypes
from aiohomekit.model.services import ServicesTypes

from .exceptions import (
    HomeKitAuthenticationError,
    HomeKitCharacteristicReadError,
    HomeKitConnectionError,
    HomeKitError,
    HomeKitNotFoundError,
    HomeKitWriteRejectedError,
)
from .models import (
    AccessoryInfo,
    CharacteristicInfo,
    Field,
    HomeKitEndpoint,
    HomeKitEvent,
    LocalAction,
    LocalMode,
)

_LOGGER = logging.getLogger(__name__)

_METADATA_REFRESH_INTERVAL = 15 * 60
_REFRESH_COOLDOWN = 60

_ENDPOINT_KEYS = ("AccessoryIP", "AccessoryIPs", "AccessoryPort")
_KNOWN_PROTOCOL_ERRORS = (HomeKitException, TimeoutError, OSError)

# All HomeKit identifiers live here. Callers use semantic Field values only.
_MAC_CHAR_UUID = "22280E2C-9B79-43BD-8370-5A8F67777B29"
_CURRENT_TEMPERATURE = CharacteristicsTypes.TEMPERATURE_CURRENT
_TARGET_TEMPERATURE = CharacteristicsTypes.TEMPERATURE_TARGET
_CURRENT_STATE = CharacteristicsTypes.HEATING_COOLING_CURRENT
_TARGET_STATE = CharacteristicsTypes.HEATING_COOLING_TARGET
_DISPLAY_UNITS = CharacteristicsTypes.TEMPERATURE_UNITS
_IDENTIFY = CharacteristicsTypes.IDENTIFY

_FIELD_TO_CHAR: dict[Field, str] = {
    Field.CURRENT_TEMPERATURE: _CURRENT_TEMPERATURE,
    Field.TARGET_TEMPERATURE: _TARGET_TEMPERATURE,
    Field.CURRENT_ACTION: _CURRENT_STATE,
    Field.TARGET_MODE: _TARGET_STATE,
    Field.DISPLAY_UNIT: _DISPLAY_UNITS,
}
_CHAR_TO_FIELD = {
    characteristic: field for field, characteristic in _FIELD_TO_CHAR.items()
}
_INFO_CHAR_FIELDS = {
    CharacteristicsTypes.NAME: "name",
    CharacteristicsTypes.MODEL: "model",
    CharacteristicsTypes.MANUFACTURER: "manufacturer",
    CharacteristicsTypes.SERIAL_NUMBER: "serial",
    CharacteristicsTypes.FIRMWARE_REVISION: "firmware",
    _MAC_CHAR_UUID: "mac",
}
_ESSENTIAL_FIELDS = frozenset(
    {
        Field.CURRENT_TEMPERATURE,
        Field.TARGET_TEMPERATURE,
        Field.CURRENT_ACTION,
        Field.TARGET_MODE,
    }
)
_ENUM_FIELDS = frozenset({Field.CURRENT_ACTION, Field.TARGET_MODE, Field.DISPLAY_UNIT})
_MODE_FROM_HAP = {
    0: LocalMode.OFF,
    1: LocalMode.HEAT,
    2: LocalMode.COOL,
    3: LocalMode.AUTO,
}
_MODE_TO_HAP = {mode: value for value, mode in _MODE_FROM_HAP.items()}


def _translate_error(error: BaseException) -> HomeKitError:
    """Translate an aiohomekit failure without leaking its private details."""
    if isinstance(error, AuthenticationError):
        return HomeKitAuthenticationError("HomeKit authentication was rejected")
    if isinstance(error, AccessoryNotFoundError):
        return HomeKitNotFoundError("HomeKit accessory was not found")
    if isinstance(
        error,
        (AccessoryDisconnectedError, EncryptionError, TimeoutError, OSError),
    ):
        return HomeKitConnectionError("HomeKit connection failed")
    if isinstance(error, HomeKitException):
        return HomeKitError("HomeKit request failed")
    return HomeKitError("HomeKit operation failed")


def _status_failed(payload: Mapping[str, Any]) -> bool:
    """Return whether the HAP status field is not successful."""
    status = payload.get("status", 0)
    if isinstance(status, int):
        return status != 0
    value = getattr(status, "value", None)
    return not isinstance(value, int) or value != 0


def _status_text(payload: Mapping[str, Any]) -> str:
    """Return a small, safe status description with no raw payload data."""
    status = payload.get("status")
    value = getattr(status, "value", status)
    return str(value) if isinstance(value, int | str) else "failed"


def _decode(field: Field, value: Any) -> Any:
    """Translate wire values into semantic Mysa values."""
    if field == Field.TARGET_MODE:
        try:
            return _MODE_FROM_HAP.get(int(value))
        except TypeError, ValueError:
            return None
    if field == Field.CURRENT_ACTION:
        try:
            # Mysa uses current-state 0 as idle, 1 as heating, and 2 as
            # cooling.
            return {
                0: LocalAction.IDLE,
                1: LocalAction.HEATING,
                2: LocalAction.COOLING,
            }.get(int(value))
        except TypeError, ValueError:
            return None
    if field == Field.DISPLAY_UNIT:
        if value == 0:
            return "C"
        if value == 1:
            return "F"
        return None
    return value


def _encode(field: Field, value: Any) -> Any:
    """Translate semantic commands to the values expected by HomeKit."""
    if field == Field.TARGET_MODE:
        try:
            return _MODE_TO_HAP[LocalMode(value)]
        except (KeyError, TypeError, ValueError) as error:
            raise HomeKitWriteRejectedError(
                "Unsupported HomeKit thermostat mode"
            ) from error
    if field == Field.DISPLAY_UNIT:
        if value == "C":
            return 0
        if value == "F":
            return 1
        raise HomeKitWriteRejectedError("Display unit must be C or F")
    return value


@dataclass(frozen=True, slots=True)
class _CharacteristicAddress:
    """Private HAP address and semantic metadata for one characteristic."""

    aid: int
    iid: int
    info: CharacteristicInfo
    valid_values: tuple[int, ...] | None = None


def _parse_accessories(
    accessories: Any,
) -> tuple[
    AccessoryInfo,
    dict[Field, _CharacteristicAddress],
    dict[Field, Any],
    tuple[int, int] | None,
]:
    """Parse Mysa's information and thermostat services into semantic values."""
    info: dict[str, str | None] = dict.fromkeys(_INFO_CHAR_FIELDS.values(), None)
    fields: dict[Field, _CharacteristicAddress] = {}
    values: dict[Field, Any] = {}
    identify: tuple[int, int] | None = None

    for accessory in accessories:
        for service in accessory.services:
            for characteristic in service.characteristics:
                characteristic_type = characteristic.type
                if service.type == ServicesTypes.ACCESSORY_INFORMATION:
                    info_field = _INFO_CHAR_FIELDS.get(characteristic_type)
                    if info_field is not None:
                        value = characteristic.value
                        if isinstance(value, str) and value:
                            info[info_field] = value.strip().strip('"')
                    elif characteristic_type == _IDENTIFY and identify is None:
                        identify = (accessory.aid, characteristic.iid)
                    continue

                if service.type != ServicesTypes.THERMOSTAT:
                    continue
                field = _CHAR_TO_FIELD.get(characteristic_type)
                if field is None:
                    continue
                valid = characteristic.valid_values
                fields[field] = _CharacteristicAddress(
                    aid=accessory.aid,
                    iid=characteristic.iid,
                    info=CharacteristicInfo(
                        min_value=(
                            None if field in _ENUM_FIELDS else characteristic.minValue
                        ),
                        max_value=(
                            None if field in _ENUM_FIELDS else characteristic.maxValue
                        ),
                        min_step=(
                            None if field in _ENUM_FIELDS else characteristic.minStep
                        ),
                    ),
                    valid_values=tuple(valid) if valid else None,
                )
                values[field] = _decode(field, characteristic.value)

    return AccessoryInfo(**info), fields, values, identify


class HomeKitDevice:
    """Semantic operations for one paired Mysa HomeKit accessory."""

    def __init__(self, pairing: Any, pairing_data: Mapping[str, Any]) -> None:
        """Wrap a raw pairing without exposing it to callers."""
        self.__pairing = pairing
        self.__pairing_data = dict(pairing_data)
        self.__info = AccessoryInfo()
        self.__fields: dict[Field, _CharacteristicAddress] = {}
        self.__values: dict[Field, Any] = {}
        self.__identify_address: tuple[int, int] | None = None
        self.__listeners: set[Callable[[HomeKitEvent], None]] = set()
        self.__endpoint_listeners: set[Callable[[HomeKitEndpoint], None]] = set()
        self.__dispatcher_unsubscribe: Callable[[], None] | None = None
        self.__config_unsubscribe: Callable[[], None] | None = None
        self.__operation_lock = asyncio.Lock()
        self.__refresh_pending = True
        self.__refreshing = False
        self.__last_refresh = float("-inf")
        self.__last_refresh_attempt = float("-inf")
        self.__last_missing_retry = float("-inf")

    @property
    def pairing_data(self) -> dict[str, Any]:
        """Return a copy for persistence and endpoint updates."""
        return dict(self.__pairing_data)

    @property
    def info(self) -> AccessoryInfo:
        """Current accessory information, if it has been read."""
        return self.__info

    @property
    def state(self) -> Mapping[Field, Any]:
        """Current semantic values as an immutable view."""
        return MappingProxyType(self.__values)

    @property
    def capabilities(self) -> Mapping[Field, CharacteristicInfo]:
        """Supported semantic fields and their advertised limits."""
        return MappingProxyType(
            {field: address.info for field, address in self.__fields.items()}
        )

    @property
    def supported_fields(self) -> frozenset[Field]:
        """Fields present in this accessory's thermostat service."""
        return frozenset(self.__fields)

    @property
    def ready(self) -> bool:
        """Whether the essential thermostat fields were discovered."""
        return _ESSENTIAL_FIELDS.issubset(self.__fields)

    @property
    def host(self) -> str | None:
        """Last known accessory address, used by the HA repair message."""
        host = self.__pairing_data.get("AccessoryIP")
        return host if isinstance(host, str) else None

    @property
    def supported_modes(self) -> tuple[LocalMode, ...]:
        """Modes advertised by the thermostat, with a safe baseboard default."""
        info = self.__fields.get(Field.TARGET_MODE)
        valid_values = info.valid_values if info else None
        values = valid_values or (0, 1)
        return tuple(
            _MODE_FROM_HAP[value] for value in values if value in _MODE_FROM_HAP
        )

    @property
    def temperature_range(self) -> tuple[float | None, float | None, float | None]:
        """Setpoint minimum, maximum and step advertised by the accessory."""
        info = self.__fields.get(Field.TARGET_TEMPERATURE)
        if info is None:
            return None, None, None
        meta = info.info
        return meta.min_value, meta.max_value, meta.min_step

    def add_listener(
        self, listener: Callable[[HomeKitEvent], None]
    ) -> Callable[[], None]:
        """Subscribe to semantic value and connection events."""
        self.__listeners.add(listener)

        def unsubscribe() -> None:
            self.__listeners.discard(listener)

        return unsubscribe

    def add_endpoint_listener(
        self, listener: Callable[[HomeKitEndpoint], None]
    ) -> Callable[[], None]:
        """Subscribe to changes in the saved accessory address and port."""
        self.__endpoint_listeners.add(listener)

        def unsubscribe() -> None:
            self.__endpoint_listeners.discard(listener)

        return unsubscribe

    async def async_get_info(self) -> AccessoryInfo:
        """Refresh the accessory database and return its identity values."""
        try:
            async with asyncio.timeout(30):
                await self.__pairing.list_accessories_and_characteristics()
        except HomeKitError:
            raise
        except _KNOWN_PROTOCOL_ERRORS as error:
            raise _translate_error(error) from error
        self.__refresh_pairing_data()
        accessories = self.__pairing.accessories
        if accessories is None:
            raise HomeKitError("HomeKit accessory did not provide its database")
        self.__info, self.__fields, self.__values, self.__identify_address = (
            _parse_accessories(accessories)
        )
        return self.__info

    async def async_connect(self) -> None:
        """Refresh characteristics and subscriptions, with bounded retries."""
        async with self.__operation_lock:
            await self.__refresh_if_needed()

    async def __refresh_if_needed(self, *, missing: bool = False) -> bool:
        now = monotonic()
        if (
            self.__last_refresh_attempt > self.__last_refresh
            and now - self.__last_refresh_attempt < _REFRESH_COOLDOWN
        ):
            return False
        if missing:
            if now - self.__last_missing_retry < _REFRESH_COOLDOWN:
                return False
            self.__last_missing_retry = now
        elif not (
            self.__refresh_pending
            or now - self.__last_refresh >= _METADATA_REFRESH_INTERVAL
        ):
            return False
        self.__last_refresh_attempt = now
        self.__refresh_pending = False
        self.__refreshing = True
        try:
            async with asyncio.timeout(15):
                await self.__refresh_metadata()
        except _KNOWN_PROTOCOL_ERRORS as error:
            self.__refresh_pending = True
            raise _translate_error(error) from error
        except HomeKitError, asyncio.CancelledError:
            self.__refresh_pending = True
            raise
        finally:
            self.__refreshing = False
        self.__last_refresh = monotonic()
        return True

    async def __refresh_metadata(self) -> None:
        await self.__pairing.async_populate_accessories_state(force_update=True)
        self.__refresh_pairing_data()
        accessories = self.__pairing.accessories
        if accessories is None:
            raise HomeKitError("HomeKit accessory did not provide its database")
        info, fields, values, identify = _parse_accessories(accessories)
        changed = fields != self.__fields
        addresses = {(address.aid, address.iid) for address in fields.values()}
        stale = set(self.__pairing.subscriptions) - addresses
        if stale:
            try:
                async with asyncio.timeout(3):
                    await self.__pairing.unsubscribe(stale)
            except _KNOWN_PROTOCOL_ERRORS as error:
                _LOGGER.debug("HomeKit unsubscribe failed (%s)", type(error).__name__)
            finally:
                # Removed characteristics can reject unsubscribe. They must also
                # leave aiohomekit's reconnect subscription set in that case.
                self.__pairing.subscriptions.difference_update(stale)
        self.__info, self.__fields, self.__identify_address = info, fields, identify
        self.__values = values
        if self.__dispatcher_unsubscribe is None:
            self.__dispatcher_unsubscribe = self.__pairing.dispatcher_connect(
                self.__handle_events
            )
        if self.__config_unsubscribe is None:
            self.__config_unsubscribe = (
                self.__pairing.dispatcher_connect_config_changed(
                    self.__handle_config_changed
                )
            )
        if addresses:
            try:
                async with asyncio.timeout(3):
                    await self.__pairing.subscribe(addresses)
            except _KNOWN_PROTOCOL_ERRORS as error:
                # Metadata and polling remain usable without event subscriptions.
                _LOGGER.debug(
                    "HomeKit event subscription failed (%s)", type(error).__name__
                )
        self.__notify(HomeKitEvent.create(values, capabilities_changed=changed))

    def __handle_config_changed(self, _config_num: int) -> None:
        self.__refresh_pending = True
        self.__notify(HomeKitEvent.create(refresh_required=True))

    async def async_read(
        self,
        fields: Iterable[Field],
        *,
        timeout: float | None = None,  # noqa: ASYNC109 - per-request read deadline
    ) -> dict[Field, Any]:
        """Read semantic fields and update the cached values on success."""
        requested = tuple(fields)
        async with self.__operation_lock:
            await self.__refresh_if_needed()
            try:
                values, missing = await self.__read_fields(requested, timeout=timeout)
            except HomeKitCharacteristicReadError as error:
                if not error.missing or not await self.__refresh_if_needed(
                    missing=True
                ):
                    raise
                values, _missing = await self.__read_fields(requested, timeout=timeout)
                return values
            if missing and await self.__refresh_if_needed(missing=True):
                values, _missing = await self.__read_fields(requested, timeout=timeout)
            return values

    async def __read_fields(
        self,
        fields: tuple[Field, ...],
        *,
        timeout: float | None,  # noqa: ASYNC109 - caller's read deadline
    ) -> tuple[dict[Field, Any], bool]:
        wanted = {
            (self.__fields[field].aid, self.__fields[field].iid): field
            for field in fields
            if field in self.__fields
        }
        absent = set(fields) - self.__fields.keys()
        if absent & _ESSENTIAL_FIELDS:
            raise HomeKitCharacteristicReadError(
                "HomeKit is missing required fields",
                absent & _ESSENTIAL_FIELDS,
                missing=True,
            )
        if not wanted:
            return {}, False
        required = {field for field in wanted.values() if field in _ESSENTIAL_FIELDS}
        try:
            async with asyncio.timeout(timeout or 3.0):
                result: Any = await self.__pairing.get_characteristics(list(wanted))
        except _KNOWN_PROTOCOL_ERRORS as error:
            raise _translate_error(error) from error
        self.__refresh_pairing_data()
        if not isinstance(result, Mapping):
            raise HomeKitCharacteristicReadError(
                "HomeKit returned an invalid response", required
            )

        values: dict[Field, Any] = {}
        failures: list[str] = []
        failed_required: set[Field] = set()
        missing = False
        for address, field in wanted.items():
            payload = result.get(address)
            if not isinstance(payload, Mapping) or _status_failed(payload):
                missing |= (
                    not isinstance(payload, Mapping)
                    or _status_text(payload) == "-70409"
                )
                if field in _ESSENTIAL_FIELDS:
                    status = (
                        _status_text(payload)
                        if isinstance(payload, Mapping)
                        else "missing"
                    )
                    failures.append(f"{field.value}: {status}")
                    failed_required.add(field)
                continue
            if "value" not in payload:
                if field in _ESSENTIAL_FIELDS:
                    failures.append(f"{field.value}: missing value")
                    failed_required.add(field)
                continue
            values[field] = _decode(field, payload["value"])

        if failures:
            raise HomeKitCharacteristicReadError(
                "HomeKit failed to read required fields: " + ", ".join(failures),
                failed_required,
                missing=missing,
            )
        self.__values.update(values)
        return values, missing

    async def async_write(self, values: Mapping[Field, Any]) -> None:
        """Write semantic fields, rejecting unsupported or failed operations."""
        async with self.__operation_lock:
            await self.__refresh_if_needed()
        # A timed-out write stays alive while HA independently reads back its
        # result. Do not hold the metadata/read lock over the network write.
        await self.__write_fields(values)

    async def __write_fields(self, values: Mapping[Field, Any]) -> None:
        payload: list[tuple[int, int, Any]] = []
        fields_by_address: dict[tuple[int, int], Field] = {}
        for field, value in values.items():
            address = self.__fields.get(field)
            if address is None:
                raise HomeKitWriteRejectedError(
                    f"HomeKit does not support {field.value}"
                )
            key = (address.aid, address.iid)
            payload.append((*key, _encode(field, value)))
            fields_by_address[key] = field
        try:
            result: Any = await self.__pairing.put_characteristics(payload)
        except _KNOWN_PROTOCOL_ERRORS as error:
            translated = _translate_error(error)
            raise translated from error
        self.__refresh_pairing_data()
        if result is not None:
            if not isinstance(result, Mapping):
                raise HomeKitWriteRejectedError(
                    "HomeKit returned an invalid write result"
                )
            failed: list[str] = []
            for key, item in result.items():
                returned_field = fields_by_address.get(key)
                if returned_field is None or not isinstance(item, Mapping):
                    raise HomeKitWriteRejectedError(
                        "HomeKit returned an unexpected write response"
                    )
                if _status_failed(item):
                    failed.append(f"{returned_field.value}: {_status_text(item)}")
            if failed:
                raise HomeKitWriteRejectedError(
                    "HomeKit refused fields: " + ", ".join(failed)
                )
        semantic_values = {
            field: values[field]
            for key, field in fields_by_address.items()
            if (current := self.__fields.get(field)) is not None
            and (current.aid, current.iid) == key
        }
        self.__values.update(semantic_values)
        self.__notify(HomeKitEvent.create(semantic_values))

    async def async_identify(self) -> None:
        """Ask the accessory to identify itself."""
        try:
            async with asyncio.timeout(6):
                if self.__identify_address is None:
                    await self.__pairing.identify()
                else:
                    aid, iid = self.__identify_address
                    result = await self.__pairing.put_characteristics(
                        [(aid, iid, True)]
                    )
                    if isinstance(result, Mapping):
                        failed = result.get((aid, iid))
                        if isinstance(failed, Mapping) and _status_failed(failed):
                            raise HomeKitWriteRejectedError(
                                "HomeKit refused the identify request"
                            )
        except HomeKitError:
            raise
        except _KNOWN_PROTOCOL_ERRORS as error:
            raise _translate_error(error) from error

    async def async_close(self) -> None:
        """Unsubscribe and close this pairing session."""
        if self.__config_unsubscribe is not None:
            self.__config_unsubscribe()
            self.__config_unsubscribe = None
        self.__refresh_pending = True
        self.__last_refresh_attempt = float("-inf")
        if self.__dispatcher_unsubscribe is not None:
            self.__dispatcher_unsubscribe()
            self.__dispatcher_unsubscribe = None
        try:
            async with asyncio.timeout(5):
                await self.__pairing.close()
        except _KNOWN_PROTOCOL_ERRORS as error:
            raise _translate_error(error) from error

    def diagnostics(self) -> dict[str, Any]:
        """Return safe, semantic values without IDs or pairing credentials."""
        return {
            "model": self.__info.model,
            "manufacturer": self.__info.manufacturer,
            "firmware": self.__info.firmware,
            "fields": {
                field.value: {
                    "value": self.__values.get(field),
                    "min_value": address.info.min_value,
                    "max_value": address.info.max_value,
                    "min_step": address.info.min_step,
                    "valid_values": tuple(
                        _decode(field, value) for value in address.valid_values
                    )
                    if address.valid_values
                    else None,
                }
                for field, address in self.__fields.items()
            },
        }

    def __handle_events(self, events: Mapping[tuple[int, int], Any]) -> None:
        """Translate a raw characteristic notification into a safe event."""
        if not events:
            # A connection opened by the refresh itself is already being mapped.
            if not self.__refreshing:
                self.__refresh_pending = True
                self.__notify(
                    HomeKitEvent.create(reconnected=True, refresh_required=True)
                )
            return
        fields_by_address = {
            (address.aid, address.iid): field
            for field, address in self.__fields.items()
        }
        values: dict[Field, Any] = {}
        failures: dict[Field, str] = {}
        accessory_changes: dict[tuple[int, int], dict[str, Any]] = {}
        for address, payload in events.items():
            field = fields_by_address.get(address)
            if not isinstance(payload, Mapping):
                continue
            failed = _status_failed(payload)
            model_payload = dict(payload)
            if failed:
                model_payload.pop("value", None)
                if field is not None:
                    failures[field] = _status_text(payload)
            elif field is not None and "value" in payload:
                value = _decode(field, payload["value"])
                values[field] = value
                self.__values[field] = value
            accessory_changes[address] = model_payload

        if events and (accessories := self.__pairing.accessories) is not None:
            try:
                accessories.process_changes(accessory_changes)
            except HomeKitException as error:
                # The event values were validated above. A model-cache problem
                # should not escape through the aiohomekit dispatcher callback.
                _LOGGER.debug(
                    "HomeKit accessory cache update failed (%s)",
                    type(error).__name__,
                )
        self.__notify(HomeKitEvent.create(values, failures, reconnected=not events))

    def __notify(self, event: HomeKitEvent) -> None:
        for listener in tuple(self.__listeners):
            listener(event)

    def __refresh_pairing_data(self) -> None:
        """Copy updated endpoint fields and notify the HA-owned callback."""
        current = getattr(self.__pairing, "pairing_data", None)
        if not isinstance(current, Mapping):
            return
        changed = {
            key: current[key]
            for key in _ENDPOINT_KEYS
            if key in current and self.__pairing_data.get(key) != current[key]
        }
        if not changed:
            return
        self.__pairing_data.update(changed)
        host = self.__pairing_data.get("AccessoryIP")
        port = self.__pairing_data.get("AccessoryPort")
        addresses = self.__pairing_data.get("AccessoryIPs")
        if not isinstance(host, str) or not isinstance(port, int):
            return
        valid_addresses = (
            tuple(value for value in addresses if isinstance(value, str))
            if isinstance(addresses, list)
            else (host,)
        )
        endpoint = HomeKitEndpoint(host, valid_addresses or (host,), port)
        for listener in tuple(self.__endpoint_listeners):
            listener(endpoint)
