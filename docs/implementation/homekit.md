# Home Assistant HomeKit integration behavior and limits

<!-- compatibility-forwarder -->

This page preserves legacy HK-* bookmarks. The [management inventory](progress.md)
owns current criteria, status, and evidence; [feature scope](../cloud-scope.md#homekit-exclusions-and-deferrals)
owns exclusions and deferrals. For pairing and code storage controls, use the
[setup guide](../guides/setup.md).

## Responsibility split

See [architecture](../architecture.md) for client and HA ownership.

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
| <a id="hk-i14"></a>**HK-I14** | Semantic diagnostics and protocol boundary | [API-LOCAL-07](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/api-coverage.md#api-local-07), [API-LOCAL-12](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/api-coverage.md#api-local-12); HA redaction is covered by [MGT-QUALITY-06](progress.md#mgt-quality-06); caller ownership is documented in [architecture](../architecture.md#testing-the-boundaries) |

## Completed reliability fixes

These former IDs map to the authoritative lifecycle requirements below.

| Former ID | Current requirement and evidence |
| --- | --- |
| <a id="hk-a01"></a>**HK-A01** | [MGT-LIFECYCLE-04](progress.md#mgt-lifecycle-04) |
| <a id="hk-a02"></a>**HK-A02** | [MGT-LIFECYCLE-05](progress.md#mgt-lifecycle-05) |
| <a id="hk-a03"></a>**HK-A03** | [MGT-LIFECYCLE-07](progress.md#mgt-lifecycle-07) |

## Deferred and excluded surfaces

| Former ID | Authoritative decision |
| --- | --- |
| <a id="hk-d01"></a>**HK-D01** | [Scope and evidence](../cloud-scope.md#hk-d01) |
| <a id="hk-d02"></a>**HK-D02** | [Scope and evidence](../cloud-scope.md#hk-d02) |
| <a id="hk-o01"></a>**HK-O01** | [Scope and evidence](../cloud-scope.md#hk-o01) |
| <a id="hk-o02"></a>**HK-O02** | [Scope and evidence](../cloud-scope.md#hk-o02) |
| <a id="hk-o03"></a>**HK-O03** | [Scope and evidence](../cloud-scope.md#hk-o03) |
| <a id="hk-o04"></a>**HK-O04** | [Scope and evidence](../cloud-scope.md#hk-o04) |

## Completion criteria and dependencies

### HK-A01 criteria

See [MGT-LIFECYCLE-04](progress.md#mgt-lifecycle-04) for the requirement, status, and regression evidence.

### HK-A02 criteria

See [MGT-LIFECYCLE-05](progress.md#mgt-lifecycle-05) for the requirement, status, and regression evidence.

### HK-A03 criteria

See [MGT-LIFECYCLE-07](progress.md#mgt-lifecycle-07) for the requirement, status, and regression evidence.

## Evidence and maintenance

See [hardware limits](progress.md#research-and-hardware-limits) and
[boundary testing](../architecture.md#testing-the-boundaries). Preserve legacy
HK-* anchors here and add new criteria only to the MGT-* inventory.
