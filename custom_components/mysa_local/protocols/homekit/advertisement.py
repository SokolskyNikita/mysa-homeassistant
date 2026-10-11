"""Parsing of HomeKit mDNS identity and endpoint data."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from ipaddress import ip_address
from typing import Any

from .exceptions import HomeKitError
from .models import HomeKitAdvertisement, HomeKitEndpoint

_ENDPOINT_KEYS = ("AccessoryIP", "AccessoryIPs", "AccessoryPort")


def parse_advertisement(
    name: str,
    properties: Mapping[str | bytes, Any],
    *,
    host: str,
    addresses: Iterable[Any],
    port: int,
) -> HomeKitAdvertisement | None:
    """Parse HomeKit TXT properties without leaking their encoding to HA."""
    if not isinstance(port, int) or not 1 <= port <= 65535:
        raise HomeKitError("HomeKit discovery had an invalid network port")
    normalized: dict[str, str] = {}
    for raw_key, raw_value in properties.items():
        if raw_value is None:
            continue
        if isinstance(raw_key, bytes):
            try:
                key = raw_key.decode("utf-8")
            except UnicodeDecodeError:
                continue
        else:
            key = raw_key
        if isinstance(raw_value, bytes):
            try:
                value = raw_value.decode("utf-8")
            except UnicodeDecodeError:
                continue
        else:
            value = raw_value
        normalized[key.casefold()] = str(value)
    pairing_id = normalized.get("id")
    model = normalized.get("md")
    if not pairing_id or not model:
        return None

    valid_addresses: list[str] = []
    for address in addresses:
        try:
            parsed = ip_address(str(address))
        except ValueError:
            continue
        if not parsed.is_link_local and not parsed.is_unspecified:
            valid_addresses.append(str(parsed))
    try:
        parsed_host = ip_address(host)
    except (TypeError, ValueError) as error:
        raise HomeKitError(
            "HomeKit discovery had an invalid network address"
        ) from error
    if parsed_host.is_link_local or parsed_host.is_unspecified:
        valid_addresses = [address for address in valid_addresses if address != host]
        if not valid_addresses:
            raise HomeKitError("HomeKit discovery had no usable network address")
        parsed_host = ip_address(valid_addresses[0])
    endpoint = HomeKitEndpoint(
        host=str(parsed_host),
        addresses=tuple(dict.fromkeys(valid_addresses)) or (str(parsed_host),),
        port=port,
    )
    try:
        paired = not bool(int(normalized.get("sf", "1")) & 0x01)
    except ValueError:
        paired = False
    return HomeKitAdvertisement(
        id=pairing_id.casefold(),
        name=name.split(".", 1)[0],
        model=model,
        paired=paired,
        endpoint=endpoint,
    )


def update_pairing_endpoint(
    pairing_data: Mapping[str, Any], endpoint: HomeKitEndpoint
) -> dict[str, Any]:
    """Return a pairing copy with its latest discovered address and port."""
    updated = dict(pairing_data)
    updated.update(
        {
            "AccessoryIP": endpoint.host,
            "AccessoryIPs": list(endpoint.addresses),
            "AccessoryPort": endpoint.port,
        }
    )
    return updated


def pairing_identity(pairing_data: Mapping[str, Any]) -> dict[str, Any]:
    """Return stable pairing fields, excluding volatile network endpoints."""
    return {
        key: value for key, value in pairing_data.items() if key not in _ENDPOINT_KEYS
    }
