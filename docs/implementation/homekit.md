# Home Assistant HomeKit integration behavior and limits

Last reviewed: **2026-10-12**, Mysa Local **0.5.2**.

The standalone [Mysa client API inventory](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/api-coverage.md) owns HomeKit request semantics, characteristic encoding and decoding, pairing operations, and protocol evidence. This page owns how Home Assistant uses those client capabilities: entity behavior, polling and recovery, persistence integration, migration, and product scope. The [HA management map](progress.md) tracks cross-protocol integration acceptance criteria.

The observed Baseboard Lite profile is `BB-V2-0-L`, firmware `3.17.5.13` (HomeKit may report `3.17.5`). Other models need their own accessory evidence. The client fixture and fake-client tests establish code behavior, not hardware compatibility. The latest live checks covered two Lites; metadata changes and migration failures remain simulated.

- [Former protocol inventory IDs](#implemented-coverage)
- [HA reliability and lifecycle criteria](#completed-reliability-fixes)
- [Deferred and excluded HomeKit behavior](#deferred-and-excluded-surfaces)
- [Completion criteria and dependencies](#completion-criteria-and-dependencies)
- [Evidence and maintenance](#evidence-and-maintenance)

## Responsibility split

| Area | Owner | Coverage |
| --- | --- | --- |
| HAP discovery, pairing methods, characteristic reads/writes, status decoding, and protocol events | [Mysa client API map](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/api-coverage.md) and [HomeKit package guide](https://github.com/SokolskyNikita/mysa-client/blob/main/src/mysa_client/homekit/README.md) | API-LOCAL-01–13; raw methods and known gaps |
| Home Assistant persistence and controller lifecycle | This integration and [MGT lifecycle rows](progress.md#mgt-lifecycle-01) | HA storage, adapter setup/unload, health, entity discovery, and migration |
| Device-specific exposed features and exclusions | This page and the [HomeKit accessory reference](../homekit.md) | Lite observations, deferred Wi-Fi migration, and deliberately unexposed services |

## Implemented coverage

These legacy HK-I anchors are retained for existing links. Their protocol scope and evidence now live in the client API inventory; links below identify the corresponding client operations and any HA-owned behavior. The previous Implemented status referred to observed code behavior, not broad HAP or model parity.

| Former ID | Protocol/integration area | Current coverage |
| --- | --- | --- |
| <a id="hk-i01"></a>**HK-I01** | Discovery and endpoint updates | [API-LOCAL-01](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/api-coverage.md#api-local-01), [API-LOCAL-02](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/api-coverage.md#api-local-02) |
| <a id="hk-i02"></a>**HK-I02** | PIN pairing and authenticated saved sessions | [API-LOCAL-03](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/api-coverage.md#api-local-03), [API-LOCAL-04](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/api-coverage.md#api-local-04); HA persistence ordering is [MGT-LIFECYCLE-06](progress.md#mgt-lifecycle-06) |
| <a id="hk-i03"></a>**HK-I03** | Copy and remove controller pairing | [API-LOCAL-05](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/api-coverage.md#api-local-05), [API-LOCAL-06](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/api-coverage.md#api-local-06); interrupted HA device migration is [MGT-LIFECYCLE-07](progress.md#mgt-lifecycle-07) |
| <a id="hk-i04"></a>**HK-I04** | Accessory identity and cloud-device matching fields | [API-LOCAL-07](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/api-coverage.md#api-local-07) |
| <a id="hk-i05"></a>**HK-I05** | Current temperature | [API-LOCAL-08](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/api-coverage.md#api-local-08); HA source selection is [MGT-STATE-01](progress.md#mgt-state-01) |
| <a id="hk-i06"></a>**HK-I06** | Target temperature | [API-LOCAL-08](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/api-coverage.md#api-local-08), [API-LOCAL-09](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/api-coverage.md#api-local-09); HA unit conversion and validation are [MGT-COMMAND-09](progress.md#mgt-command-09) |
| <a id="hk-i07"></a>**HK-I07** | Current action and target mode | [API-LOCAL-08](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/api-coverage.md#api-local-08), [API-LOCAL-09](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/api-coverage.md#api-local-09), [API-LOCAL-12](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/api-coverage.md#api-local-12) |
| <a id="hk-i08"></a>**HK-I08** | Display units and entity recovery | [API-LOCAL-09](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/api-coverage.md#api-local-09); HA discovery after recovery is [MGT-LIFECYCLE-05](progress.md#mgt-lifecycle-05) |
| <a id="hk-i09"></a>**HK-I09** | Identify | [API-LOCAL-10](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/api-coverage.md#api-local-10) |
| <a id="hk-i10"></a>**HK-I10** | Characteristic discovery and capability metadata | [API-LOCAL-12](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/api-coverage.md#api-local-12); HA refresh lifecycle is [MGT-LIFECYCLE-04](progress.md#mgt-lifecycle-04) |
| <a id="hk-i11"></a>**HK-I11** | Reads, events, and error/status handling | [API-LOCAL-08](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/api-coverage.md#api-local-08), [API-LOCAL-11](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/api-coverage.md#api-local-11); HA read health is [MGT-LIFECYCLE-03](progress.md#mgt-lifecycle-03) |
| <a id="hk-i12"></a>**HK-I12** | Writes and uncertain command outcomes | [API-LOCAL-09](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/api-coverage.md#api-local-09); HA serialization, readback, and routing are [MGT-COMMAND-01–05](progress.md#mgt-command-01) |
| <a id="hk-i13"></a>**HK-I13** | Session lifecycle, polling, and local recovery | [API-LOCAL-11](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/api-coverage.md#api-local-11); HA controller/session lifecycle and polling are [MGT-LIFECYCLE-01–03](progress.md#mgt-lifecycle-01) |
| <a id="hk-i14"></a>**HK-I14** | Semantic diagnostics and protocol boundary | [API-LOCAL-07](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/api-coverage.md#api-local-07), [API-LOCAL-12](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/api-coverage.md#api-local-12); HA redaction and caller boundary are covered by [MGT-QUALITY-05](progress.md#mgt-quality-05) and [MGT-QUALITY-06](progress.md#mgt-quality-06) |

## Completed reliability fixes

These HA integration criteria are retained separately from client protocol support. All three are implemented in Mysa Local 0.5.2; their hardware and failure-path limits remain explicit.

| ID / status | HA behavior | Evidence |
| --- | --- | --- |
| <a id="hk-a01"></a>**HK-A01** · Implemented (0.5.0) | Consume client capability/configuration refreshes, update HA's selected fields and supported controls, keep polling available when subscriptions fail, and reconcile metadata on reconnect plus a 15-minute HA backstop. [Criteria](#hk-a01-criteria). | [Local adapter](../../custom_components/mysa_local/local_adapter.py), [refresh tests](../../tests/test_homekit_refresh.py), [client API-LOCAL-11](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/api-coverage.md#api-local-11) |
| <a id="hk-a02"></a>**HK-A02** · Implemented (0.5.0) | Create newly available capability entities after local recovery, without reload or duplicates, and remove discovery listeners on unload. [Criteria](#hk-a02-criteria). | [Entity helper](../../custom_components/mysa_local/entity.py), [recovery tests](../../tests/test_homekit_refresh.py), [MGT-LIFECYCLE-05](progress.md#mgt-lifecycle-05) |
| <a id="hk-a03"></a>**HK-A03** · Implemented (0.5.0) | Preserve and retry an interrupted HomeKit Device migration without losing destination entities, entity IDs, or unrelated device metadata. [Criteria](#hk-a03-criteria). | [Migration](../../custom_components/mysa_local/migration.py), [migration tests](../../tests/test_migration.py), [MGT-LIFECYCLE-07](progress.md#mgt-lifecycle-07) |

## Deferred and excluded surfaces

These rows retain device-specific scope decisions. Raw characteristic layouts and protocol transcripts belong in the client [HomeKit reference](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/homekit.md) and sanitized fixtures.

| ID / status | Surface | HA decision and evidence |
| --- | --- | --- |
| <a id="hk-d01"></a>**HK-D01** · Deferred | Wi-Fi Transport / Configuration Control (`0x22A` / `0x22D`) | No semantic configuration API or HA Wi-Fi migration flow. A staged operation-3 write was accepted, but a successful network move was not established; operation 2 and completed operation 4 remain unverified. See the [client protocol findings](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/homekit.md#homekit-wi-fi-switching) and [API-LOCAL-13](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/api-coverage.md#api-local-13). |
| <a id="hk-d02"></a>**HK-D02** · Deferred | Hidden vendor boolean | Purpose and physical effect remain unknown; do not expose as a named HA feature. Evidence and the sanitized accessory profile are in the [client accessory reference](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/homekit.md#characteristic-coverage) and [fixture](https://github.com/SokolskyNikita/mysa-client/blob/main/tests/fixtures/homekit_bb_v2_0_l.json). |
| <a id="hk-o01"></a>**HK-O01** · Out of scope | Accessory Runtime Information (`0x239`) and extra information fields | Not thermostat controls or HA entities without a useful consumer; see [API-LOCAL-07](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/api-coverage.md#api-local-07) and the [client accessory profile](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/homekit.md#characteristic-coverage). |
| <a id="hk-o02"></a>**HK-O02** · Out of scope on observed Lite | Humidity, button lock, brightness, schedules, and other cloud functions | These were absent from the observed Lite HomeKit profile. Do not infer local equivalents for cloud fields; revisit only with a different verified accessory. See [HomeKit findings](../homekit.md#characteristic-coverage) and the [HA cloud scope map](../cloud-scope.md). |
| <a id="hk-o03"></a>**HK-O03** · Out of scope | Firmware installation through HomeKit | No established HomeKit update operation; HomeKit reads firmware metadata only. Firmware update methods and evidence are tracked in the [client API map](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/api-coverage.md#api-read-19) and [firmware reference](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/firmware-and-connectivity.md). |
| <a id="hk-o04"></a>**HK-O04** · Out of scope | BLE provisioning, Matter, and a new HAP transport | They are separate setup/protocol scopes. This adapter uses HAP over IP through `aiohomekit`; see the client [BLE](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/bluetooth.md), [Matter](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/matter.md), and [HomeKit package](https://github.com/SokolskyNikita/mysa-client/blob/main/src/mysa_client/homekit/README.md) references. |

## Completion criteria and dependencies

### HK-A01 criteria

**Completion:** On HomeKit configuration/reconnection changes, let the client refresh characteristic mappings, capabilities, and subscriptions; consume the resulting update in HA. HA's 15-minute backstop must refresh integration state, removed fields must disappear, capability changes must rerun discovery, and polling must continue if subscriptions fail. Avoid duplicate listeners, entities, or refresh loops. Cover the adapter triggers and recovery behavior with HA tests; the client's protocol-level refresh mechanics are covered in its own suite.

**Status:** completed in 0.5.0. HA regression tests cover refresh triggers, removed values, subscription failure, entity effects, and unload cancellation. Client protocol tests separately cover address/metadata changes and bounded retries. Changed metadata has not been observed on the tested Lites; hardware validation remains separate.

### HK-A02 criteria

**Completion:** With cloud disabled and the thermostat initially offline, create the display-unit select after local capability discovery recovers, without reload. Repeated recovery must not create duplicate entities; unregister discovery listeners on unload. Cover this with a fake local connection and HA entity tests.

**Status:** completed in 0.5.0. Tests cover cloud-disabled offline startup, recovery, repeated notifications, entity deduplication, and listener cleanup.

### HK-A03 criteria

**Completion:** Preserve `CONF_MIGRATE_FROM` when unloading or takeover fails; clear it only when migration is complete or the source is already absent. Test failure followed by retry, existing destination entities, and successful cleanup. Preserve entity IDs and unrelated device metadata.

**Status:** completed in 0.5.0. Tests cover failed unload followed by retry, interrupted source removal, existing destination entities, preserved IDs/device metadata, and an already-absent source.

## Evidence and maintenance

Use the client [sanitized Lite fixture](https://github.com/SokolskyNikita/mysa-client/blob/main/tests/fixtures/homekit_bb_v2_0_l.json) for accessory shapes and the client [HomeKit protocol tests](https://github.com/SokolskyNikita/mysa-client/blob/main/tests/homekit/test_homekit_protocol.py) for method and codec behavior. HA [routing](../../tests/test_routing.py), [read-error](../../tests/test_local_read_errors.py), [refresh](../../tests/test_homekit_refresh.py), and [migration](../../tests/test_migration.py) tests validate integration policy. These tests do not establish support for every HAP service or model.

Keep API semantics and wire details with the client. Keep Home Assistant's polling, entity behavior, lifecycle, migration, and product decisions here and in the [management map](progress.md). Preserve legacy HK-* anchors when redirecting links, and add future HA acceptance criteria to the MGT-* inventory.
