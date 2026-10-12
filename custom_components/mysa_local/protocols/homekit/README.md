# Mysa HomeKit protocol

This package implements the integration's local Mysa control through HomeKit
Accessory Protocol (HAP) over IP. It uses `aiohomekit` for controller operation,
mDNS discovery, pairing exchanges, encrypted accessory sessions, and HAP
requests. It translates Mysa's accessory database and those operations into a
semantic API; it does not implement the underlying HAP transport or cryptography.

For Mysa-specific hardware observations, unsupported characteristics, and Wi-Fi
migration limits, see the [accessory reference](../../../../docs/homekit.md).

## How it is organized

- [`controller.py`](controller.py) wraps `aiohomekit.Controller`: discovery,
  PIN pairing, loading saved credentials, unpairing, and copying a controller
  pairing. It translates library failures into package exceptions.
- [`device.py`](device.py) parses the accessory database, maps thermostat
  characteristics, encodes commands, and handles cached state and notifications.
- [`advertisement.py`](advertisement.py) parses plain mDNS/TXT data supplied by
  the HA adapter and updates endpoint values in saved pairing data. Its stable
  pairing identity excludes changing network addresses.
- [`models.py`](models.py) and [`exceptions.py`](exceptions.py) define semantic
  records and errors. [`__init__.py`](__init__.py) exports the public API.

## Characteristics and device operations

An accessory describes its services and characteristics, each addressed by an
accessory ID and instance ID. `device.py` locates the thermostat service and
builds a private address map. Callers use `Field` values instead:

| Semantic field | HAP characteristic and interpretation |
| --- | --- |
| `CURRENT_TEMPERATURE` | Current Temperature; numeric Celsius |
| `TARGET_TEMPERATURE` | Target Temperature; numeric Celsius |
| `CURRENT_ACTION` | Current Heating/Cooling State: 0 = idle, 1 = heating, 2 = cooling |
| `TARGET_MODE` | Target Heating/Cooling State: 0 = off, 1 = heat, 2 = cool, 3 = auto |
| `DISPLAY_UNIT` | Temperature Display Units: 0 = `"C"`, 1 = `"F"` |

Display units do not change the Celsius values of temperature characteristics.
Modes and actions are exposed as `LocalMode` and `LocalAction`; unknown mode,
action, or unit encodings decode to `None`. Accessory-information characteristics
supply model, manufacturer, serial, and firmware. A Mysa-specific MAC
characteristic supplies the Wi-Fi identity used to match a cloud device.

Given an already-started `HomeKitProtocol` and saved credentials, this example
connects, reads temperature, and closes the session:

```python
from custom_components.mysa_local.protocols.homekit import Field, HomeKitProtocol


async def read_temperature(protocol: HomeKitProtocol, saved_pairing: dict):
    device = protocol.load_pairing(saved_pairing)
    try:
        await device.async_connect()
        values = await device.async_read([Field.CURRENT_TEMPERATURE])
        return values[Field.CURRENT_TEMPERATURE]
    finally:
        await device.async_close()
```

`async_connect()` loads the accessory database and subscribes to events. Reads
update the semantic cache but do not emit value events. A failed or missing
requested essential field raises `HomeKitCharacteristicReadError`; a missing
optional display-unit reading can be omitted. `state` is a read-only view of
the latest observed or accepted values, not a freshness guarantee.

A command such as
`await device.async_write({Field.TARGET_TEMPERATURE: 20.5})` changes the target.
Writes reject unsupported fields and invalid mode/unit encodings, inspect
per-characteristic failure statuses, and update the cache and listeners when
accepted. They do not perform an independent readback. Numeric range validation
and command policy belong to the caller. Notifications become immutable
`HomeKitEvent` objects with semantic values and failed fields; error text may
retain a HAP status code, but callers do not need to interpret wire payloads.

Capabilities expose available fields, supported modes, and advertised numeric
setpoint limits and step. `ready` means essential characteristics exist, not
that telemetry is fresh. `diagnostics()` returns model, manufacturer, firmware,
semantic values, and field metadata without pairing credentials or HAP IDs.

## Discovery and pairing persistence

HAP IP accessories advertise `_hap._tcp.local.` over mDNS. The TXT record's
`id` identifies the accessory, `md` names its model, and `sf` indicates pairing
availability. Saved pairing keys authorize later sessions without another PIN
exchange; their endpoint can change when DHCP or discovery supplies a new address.

`parse_advertisement()` requires identity/model TXT fields, normalizes them,
and validates the endpoint, rejecting invalid ports or unusable addresses.
`HomeKitProtocol.async_discover()` and `async_find()` expose discovery records;
`async_start_pairing()` starts the PIN exchange and returns a `PairingSession`.
Saved pairing data remains an opaque credential dictionary for callers to store.

Once the accessory accepts the controller key, `PairingSession.async_complete()`
invokes its required `on_pairing_saved` callback before requesting accessory
details. It awaits and shields the callback from cancellation; the caller owns
storage and durability. If persistence fails, the protocol attempts bounded
removal of the newly added key and re-raises the original error. Pair-copy uses
the same persistence rule and attempts cleanup when an add request fails
ambiguously. Pairing rollback and device-session close have time limits;
controller shutdown delegates directly to `aiohomekit`.

This coordination is best effort: an unreachable accessory may retain a key
when rollback fails. Cleanup errors are logged without replacing the original
failure. If persistence succeeds but the accessory-information read fails,
credentials are retained so setup can resume without pairing again. HA's config
flow and [`pairing_store.py`](../../pairing_store.py) own recovery and storage.

## Home Assistant lifecycle and policy

[`homekit_adapter.py`](../../homekit_adapter.py) obtains HA's shared asynchronous
Zeroconf instance, starts one shared `HomeKitProtocol`, and stops it at HA
shutdown. It adapts advertisements and delegates copy/unpair operations.
[`local_adapter.py`](../../local_adapter.py) owns polling, event callbacks,
availability, background writes, and device shutdown. Subscription failure can
fall back to caller-managed polling; this package has no polling loop.

Known transport/authentication errors become `HomeKitError` subclasses. Reads
have a default three-second deadline; writes leave deadline and retry policy
to the caller. [`command_router.py`](../../command_router.py) coordinates local
write deadlines, cloud fallback, and late local completion. The thermostat and
router consume semantic fields rather than calling `aiohomekit` directly.

The protocol modules import neither HA nor the cloud package. They are bundled
inside the integration, so importing through `custom_components.mysa_local`
still initializes the HA parent package. See the
[architecture overview](../../../../docs/architecture.md) for the surrounding
layers.

## Tests

- [`test_homekit_protocol.py`](../../../../tests/test_homekit_protocol.py):
  parsing, encoding, invalid values, read failures, advertisements, and pairing
  persistence/rollback behavior using fakes.
- [`test_pairing_protocol_failures.py`](../../../../tests/test_pairing_protocol_failures.py):
  lost replies and cancellation after a pairing-add request.
- [`test_homekit.py`](../../../../tests/test_homekit.py): HA adapter lifecycle.
- [`test_protocol_boundaries.py`](../../../../tests/test_protocol_boundaries.py):
  import separation and confinement of direct `aiohomekit` use.

These tests establish controlled behavior; real-device validation is a separate
check, and compatibility varies with the accessory's exposed characteristics.
