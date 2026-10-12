# HomeKit implementation status and plan

Last reviewed: **2026-10-12**, code commit `de311b8`. This compares the known
Mysa HomeKit surface with `protocols.homekit` and its HA callers. It is not a
claim of complete HAP support or parity across every Mysa model.

The [accessory reference](../homekit.md) owns hardware findings and wire details;
the [package guide](../../custom_components/mysa_local/protocols/homekit/README.md)
owns the semantic API and lifecycle contract. `aiohomekit` supplies discovery,
pairing exchanges, encryption, and HAP transport. The Mysa package maps accessory
characteristics into thermostat operations; HA owns polling, entities, storage,
and command routing.

**Core Lite thermostat parity is implemented:** current/target temperature,
current action, target mode, display units, and Identify. The main protocol-layer
gap is refreshing characteristic metadata and subscriptions after configuration
changes. Additional advertised services are not automatically thermostat features.

Hardware evidence covers Baseboard Lite `BB-V2-0-L`, firmware `3.17.5.13`
(HomeKit may report `3.17.5`). Other models require their own accessory profiles
and volunteer hardware validation. Fixtures and fake-client tests establish code
behavior, not broader hardware compatibility.

- [Implemented coverage](#implemented-coverage)
- [Needs adjustment](#needs-adjustment)
- [Deferred and excluded surfaces](#deferred-and-excluded-surfaces)
- [Completion criteria and dependencies](#completion-criteria-and-dependencies)
- [Evidence and maintenance](#evidence-and-maintenance)

Statuses: **Implemented** covers the named operation; **Needs adjustment** marks
an existing implementation gap; **Deferred** requires new evidence before feature
work; **Out of scope** has no selected client use. A successful HAP write is request
acceptance, not independent readback or proof of every physical effect.

## Implemented coverage

| ID / status | Protocol feature | Current implementation and limits | Evidence |
| --- | --- | --- | --- |
| <a id="hk-i01"></a>**HK-I01** · Implemented | IP discovery and endpoint changes | `async_discover()` / `async_find()` and `parse_advertisement()` expose accessory identity, model, pairing status and usable IP/port. Saved endpoints can change without changing pairing identity. Discovery does not prove possession of valid pairing keys. | [Controller](../../custom_components/mysa_local/protocols/homekit/controller.py), [advertisements](../../custom_components/mysa_local/protocols/homekit/advertisement.py), [protocol tests](../../tests/test_homekit_protocol.py) |
| <a id="hk-i02"></a>**HK-I02** · Implemented | PIN pairing and saved authenticated sessions | `PairingSession.async_complete()` persists accepted credentials before accessory reads; `load_pairing()` reuses saved keys. The save callback is awaited and shielded. Storage failure triggers bounded, best-effort key removal; failed rollback can leave a key on an unreachable accessory. HA owns durable storage. | [Controller](../../custom_components/mysa_local/protocols/homekit/controller.py), [pairing store](../../custom_components/mysa_local/pairing_store.py), [protocol tests](../../tests/test_homekit_protocol.py) |
| <a id="hk-i03"></a>**HK-I03** · Implemented | Add/remove controller pairing | `async_copy_pairing()` adds a separate admin key through an existing IP pairing; `async_unpair()` removes this controller's pairing. Lost add replies, cancellation, and persistence failure receive bounded cleanup. This is not a general pairing-management UI. HA entity migration has a separate retry gap, [HK-A03](#hk-a03). | [Controller](../../custom_components/mysa_local/protocols/homekit/controller.py), [failure tests](../../tests/test_pairing_protocol_failures.py), [HA adapter tests](../../tests/test_homekit.py) |
| <a id="hk-i04"></a>**HK-I04** · Implemented | Accessory information and cloud-device matching | Parses name, manufacturer, model, serial, firmware, and the Mysa Wi-Fi MAC characteristic. `AccessoryInfo.device_id` normalizes that MAC for matching; it is distinct from the HomeKit accessory identity. Information is refreshed when the accessory database is loaded. | [Device parser](../../custom_components/mysa_local/protocols/homekit/device.py), [models](../../custom_components/mysa_local/protocols/homekit/models.py), [sanitized fixture](../../tests/fixtures/homekit_bb_v2_0_l.json) |
| <a id="hk-i05"></a>**HK-I05** · Implemented | Current temperature (`0x11`) | `CURRENT_TEMPERATURE` supports reads and events in Celsius. HA uses it for climate and temperature readings; changing display units does not change the wire unit. | [Device](../../custom_components/mysa_local/protocols/homekit/device.py), [thermostat](../../custom_components/mysa_local/thermostat.py), [protocol tests](../../tests/test_homekit_protocol.py) |
| <a id="hk-i06"></a>**HK-I06** · Implemented | Target temperature (`0x35`) | `TARGET_TEMPERATURE` supports reads, writes, and events. Advertised minimum, maximum and step are exposed; the Lite profile is 5–30 °C with a 0.5 °C step. The generic protocol writer does not enforce numeric ranges; callers own validation. HA display increments and converted command precision are separate. | [Device](../../custom_components/mysa_local/protocols/homekit/device.py), [thermostat](../../custom_components/mysa_local/thermostat.py), [temperature-unit tests](../../tests/test_temperature_units.py) |
| <a id="hk-i07"></a>**HK-I07** · Implemented | Current action (`0x0F`) and target mode (`0x33`) | Decodes action as idle/heating/cooling and target mode as off/heat/cool/auto; target modes can be written. Advertised `valid-values` determine supported modes, with off/heat as the fallback. Only off/heat is hardware-established on Lite; cool/auto codecs do not establish other-model support. | [Device](../../custom_components/mysa_local/protocols/homekit/device.py), [models](../../custom_components/mysa_local/protocols/homekit/models.py), [protocol tests](../../tests/test_homekit_protocol.py) |
| <a id="hk-i08"></a>**HK-I08** · Implemented | Display units (`0x36`) | `DISPLAY_UNIT` maps 0/1 to C/F for reads, writes and events, and HA exposes a configuration select. A missing optional unit read does not fail essential thermostat reads. Entity creation after an offline startup needs [HK-A02](#hk-a02). | [Device](../../custom_components/mysa_local/protocols/homekit/device.py), [select](../../custom_components/mysa_local/select.py), [routing tests](../../tests/test_routing.py), [read-error tests](../../tests/test_local_read_errors.py) |
| <a id="hk-i09"></a>**HK-I09** · Implemented | Identify (`0x14`) | `async_identify()` writes the discovered Identify characteristic, or delegates to the pairing's Identify operation when no address was parsed. HA exposes an Identify button. | [Device](../../custom_components/mysa_local/protocols/homekit/device.py), [button](../../custom_components/mysa_local/button.py), [routing tests](../../tests/test_routing.py) |
| <a id="hk-i10"></a>**HK-I10** · Implemented | Characteristic discovery and capability metadata | Parses thermostat addresses, available semantic fields, supported modes and temperature limits on connection. `ready` requires the four essential thermostat fields; it does not establish freshness. Ongoing metadata refresh is [HK-A01](#hk-a01). | [Device](../../custom_components/mysa_local/protocols/homekit/device.py), [protocol tests](../../tests/test_homekit_protocol.py) |
| <a id="hk-i11"></a>**HK-I11** · Implemented | Reads, notifications and HAP statuses | Translates known addresses into immutable semantic events. Failed event values do not overwrite good values; failed/missing requested essential readings raise a typed error. Unknown mode/action/unit values decode to `None`. Ordinary reads update the cache without emitting events; the HA adapter notifies its listeners. | [Device](../../custom_components/mysa_local/protocols/homekit/device.py), [local adapter](../../custom_components/mysa_local/local_adapter.py), [read-error tests](../../tests/test_local_read_errors.py) |
| <a id="hk-i12"></a>**HK-I12** · Implemented | Writes and uncertain outcomes | Checks returned per-characteristic statuses, rejects unsupported fields and invalid mode/unit encodings, then updates the semantic cache on acceptance. There is no unconditional protocol readback. HA serializes commands, reads back uncertain local results before cloud fallback, and reconciles late writes against the latest intent. | [Device](../../custom_components/mysa_local/protocols/homekit/device.py), [router](../../custom_components/mysa_local/command_router.py), [routing tests](../../tests/test_routing.py), [ordering tests](../../tests/test_write_ordering.py) |
| <a id="hk-i13"></a>**HK-I13** · Implemented | Session lifecycle, polling and recovery | HA shares one controller, starts device subscriptions, polls every 30 seconds and closes sessions on unload. Subscription failure permits polling. Essential read/event failures mark the local path unavailable; successful recovery reads restore it. Reconnection alone does not guarantee metadata remapping, [HK-A01](#hk-a01). | [HA adapter](../../custom_components/mysa_local/homekit_adapter.py), [local adapter](../../custom_components/mysa_local/local_adapter.py), [adapter tests](../../tests/test_homekit.py), [read-error tests](../../tests/test_local_read_errors.py) |
| <a id="hk-i14"></a>**HK-I14** · Implemented | Semantic diagnostics and protocol boundaries | Package diagnostics expose model, firmware, field values and advertised metadata without pairing credentials or characteristic addresses. HA redacts household identifiers. HAP access stays inside `protocols.homekit`; it imports neither HA nor the cloud protocol. | [Device](../../custom_components/mysa_local/protocols/homekit/device.py), [diagnostics](../../custom_components/mysa_local/diagnostics.py), [redaction tests](../../tests/test_features.py), [boundary tests](../../tests/test_protocol_boundaries.py) |

## Needs adjustment

These gaps do not mean basic thermostat control is missing. The last two belong
to HA integration lifecycle rather than the standalone HomeKit protocol.

| ID / status | Gap | Current behavior and intended change | Evidence |
| --- | --- | --- | --- |
| <a id="hk-a01"></a>**HK-A01** · Needs adjustment | Characteristic metadata and subscription refresh | Connection parses the database, but an empty reconnect event only announces reconnection. The HA poll calls `async_connect()` only while essential fields are missing; otherwise it reads the existing mapping. Add deliberate refresh after configuration changes/reconnection, a 15-minute backstop, and bounded refresh/retry for missing characteristics. Replace metadata, values and subscriptions together. **Deferred until cloud reliability work is complete.** [Criteria](#hk-a01-criteria). | [Device](../../custom_components/mysa_local/protocols/homekit/device.py), [advertisements](../../custom_components/mysa_local/protocols/homekit/advertisement.py), [local adapter](../../custom_components/mysa_local/local_adapter.py) |
| <a id="hk-a02"></a>**HK-A02** · Needs adjustment | Entity discovery after local recovery | `async_add_when_ready()` runs entity factories at setup and on cloud updates only. Local capability recovery does not rerun them, so a local-only display-unit select can stay absent after an offline startup. Subscribe entity discovery to relevant local capability changes. [Criteria](#hk-a02-criteria). | [Entity helper](../../custom_components/mysa_local/entity.py), [select](../../custom_components/mysa_local/select.py), [local adapter](../../custom_components/mysa_local/local_adapter.py) |
| <a id="hk-a03"></a>**HK-A03** · Needs adjustment | Retry after incomplete HomeKit Device migration | `_async_move()` can return when the old entry cannot unload, but its caller still removes the migration marker. Preserve the marker until migration finishes. This concerns HA registry takeover, not Wi-Fi migration or the pairing-copy wire operation. [Criteria](#hk-a03-criteria). | [Migration](../../custom_components/mysa_local/migration.py), [setup lifecycle](../../custom_components/mysa_local/__init__.py) |

## Deferred and excluded surfaces

| ID / status | Observed or requested surface | Parity and scope | Evidence |
| --- | --- | --- | --- |
| <a id="hk-d01"></a>**HK-D01** · Deferred | Wi-Fi Transport / Configuration Control (`0x22A` / `0x22D`) | No semantic adapter API or HA Wi-Fi migration feature. Historical operation-1 reads and operation-3 acceptance did not establish a successful network move; operation 2 and completed operation 4 remain unverified. More validation is required before selecting an implementation. The fixture is not a complete Wi-Fi-configuration transcript. | [Wi-Fi findings](../homekit.md#homekit-wi-fi-switching), [device](../../custom_components/mysa_local/protocols/homekit/device.py) |
| <a id="hk-d02"></a>**HK-D02** · Deferred | Hidden vendor boolean | Not mapped or exposed. Its purpose remains unknown; retained boolean writes did not establish lock, display, reboot or connectivity effects. A writable characteristic is insufficient evidence for a named feature. | [Characteristic findings](../homekit.md#characteristic-coverage), [fixture](../../tests/fixtures/homekit_bb_v2_0_l.json), [device](../../custom_components/mysa_local/protocols/homekit/device.py) |
| <a id="hk-o01"></a>**HK-O01** · Out of scope | Accessory Runtime Information (`0x239`) and additional information fields | The semantic adapter does not expose runtime ping/heartbeat, protocol version, hardware revision, vendor revision or signature data as controls/entities. Presence in the fixture does not establish an additional useful thermostat feature; there is no selected consumer. | [Fixture](../../tests/fixtures/homekit_bb_v2_0_l.json), [parser](../../custom_components/mysa_local/protocols/homekit/device.py) |
| <a id="hk-o02"></a>**HK-O02** · Out of scope on the observed Lite | Humidity, button lock, brightness, schedules and other cloud functions | These are absent from the established Lite HomeKit surface, so their absence from the adapter is not a missing known HomeKit implementation. Use the supported cloud operations. Do not infer local equivalents for cloud fields; revisit only with a different verified accessory profile. | [Accessory findings](../homekit.md#characteristic-coverage), [cloud tracker](cloud.md) |
| <a id="hk-o03"></a>**HK-O03** · Out of scope | Firmware installation through HomeKit | No established Mysa HomeKit firmware-update operation; the adapter reads firmware metadata only. Firmware checks/updates use the cloud/device paths, whose limits are tracked separately. | [Firmware reference](../firmware-and-connectivity.md), [cloud update gap](cloud.md#c-a01) |
| <a id="hk-o04"></a>**HK-O04** · Out of scope | BLE provisioning, Matter and a new HAP transport stack | The package supports HAP over IP through `aiohomekit`. BLE provisioning belongs to the setup product; Matter is a separate protocol. Neither is missing HomeKit thermostat parity. | [Package guide](../../custom_components/mysa_local/protocols/homekit/README.md), [Bluetooth reference](../bluetooth.md) |

## Completion criteria and dependencies

### HK-A01 criteria

**Completion:** refresh mappings, advertised limits/modes and subscriptions after
configuration changes and reconnects, plus a 15-minute backstop. Retry a missing
characteristic once after a refresh with a cooldown. Remove stale values and
subscriptions when a characteristic disappears; avoid duplicate listeners and
refresh loops. Mock address changes, removed fields, changed limits, and repeated
reconnections. Preserve polling when subscriptions fail.

**Dependencies:** coordination between protocol events/metadata and HA lifecycle.
Scheduled after cloud reliability work. Stale metadata was reproduced in
simulation, not observed on the tested Lites; hardware validation is separate
from completing the deterministic regression cases.

### HK-A02 criteria

**Completion:** with cloud disabled and the thermostat initially offline, create
the display-unit select after local capability discovery recovers, without a
reload. Repeated recovery must not create duplicate entities; unregister discovery
listeners on unload. Cover this with a fake local connection and HA entity tests.

**Dependencies:** a local capability-change notification and the entity factory
listener. Initial-recovery handling can be fixed independently of HK-A01; later
metadata changes should use the same mechanism.

### HK-A03 criteria

**Completion:** preserve `CONF_MIGRATE_FROM` when unloading or takeover fails;
clear it only when migration is complete or the source is already absent. Test
failure followed by retry, existing destination entities, and successful cleanup.
Preserve entity IDs and unrelated device metadata.

**Dependencies:** the migration helper must report completion to its caller.
No new pairing protocol is needed.

## Evidence and maintenance

The [Lite fixture](../../tests/fixtures/homekit_bb_v2_0_l.json) supplies sanitized
accessory shapes. [Protocol tests](../../tests/test_homekit_protocol.py) cover
semantic values, unknown enums, read failures, advertisements and pairing-save
ordering. [Pairing failure tests](../../tests/test_pairing_protocol_failures.py)
cover ambiguous add replies and cancellation. HA [routing](../../tests/test_routing.py),
[read-error](../../tests/test_local_read_errors.py), and
[write-ordering](../../tests/test_write_ordering.py) tests exercise caller policy.
Those files do not prove every HAP service or physical effect is supported.

Keep stable IDs when statuses change. Update implementation rows with the code
and relevant regression evidence; retain hardware limits beside each claim.
Deferred protocol investigations remain in the workspace TODO, with no selected
implementation until their meaning or behavior is established. The
[architecture guide](../architecture.md) owns local/cloud routing and persistence;
the [cloud tracker](cloud.md) owns cloud-specific coverage.
