# Documentation maintenance

These protocols were reverse-engineered from the original Mysa apps and checked
against hardware observations where available. Each reference distinguishes
app-derived behavior from confirmed device behavior.

## Authority and locations

| Material | Maintained home |
| --- | --- |
| Cloud inventory, S/R/L comparisons, limitations and recommended selection policy | [Cloud README](https://github.com/SokolskyNikita/mysa-client/blob/main/src/mysa_client/cloud/README.md) |
| Cloud implementation status, gaps, dependencies and exclusions | [Cloud tracker](implementation/cloud.md) |
| Raw API method coverage and integration/management coverage maps | [Coverage overview](implementation/progress.md); regenerate with `python3 tools/update_api_progress.py` |
| Cognito wire exchange/browser limits | [Authentication](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/cloud-auth.md) |
| State/usage/capability shapes and model enums | [State reference](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/cloud-state.md) |
| Schedule/account workflow mechanics | [Workflows](cloud-workflows.md) |
| BLE and SoftAP provisioning | [Bluetooth](bluetooth.md); implementation owned by the separate setup repository |
| HomeKit feature parity, implementation gaps and exclusions | [HomeKit tracker](implementation/homekit.md) |
| Mysa HomeKit accessory findings | [HomeKit](homekit.md); adapter lifecycle stays in its package guide |
| MQTT authorization and wire reference | [MQTT](mqtt.md) |
| Device startup, resilience, firmware checks and OTA | [Firmware/connectivity](firmware-and-connectivity.md) |
| Integration ownership/polling/fallback/persistence | [Architecture](architecture.md), [user guide](../README.md) |
| Substantial exchanges | [JSON examples](examples/README.md); existing regression fixtures retained |


## Evidence and scope

Hardware findings currently cover Baseboard Lite `BB-V2-0-L`, firmware
`3.17.5.13`. Model mappings recovered from the apps do not establish equivalent
hardware support. App versions remain beside version-dependent protocol details.

Distinguish API acceptance, stored records, device reports, independent local
confirmation, and end-to-end behavior. Keep observation windows beside claims
when they affect interpretation. Missing metadata stays unknown; examples must
not imply observations that were never made.

## Ongoing checks and review

Run `python3 tools/check_docs.py` and `git diff --check`. The documentation checker
uses only local files and Python's standard library: strict JSON, exchange shape,
body/bodyFile exclusivity, payload paths, evidence metadata, IDs, reachability,
local Markdown links/anchors, table/fence structure, and obvious secret/identifier
patterns. It never imports or calls a device/client implementation. External URLs
are not network-checked. Semantic accuracy and sanitization still require review.

When coverage changes, update the appropriate API or management row in the
[coverage inventories](implementation/progress.md) and run
`python3 tools/update_api_progress.py`. Its `--check` mode verifies that the
embedded summary and both SVG panels match the inventories without changing any
files. Keep raw method availability separate from orchestration, regression
criteria and hardware validation; neither score is a bug-free percentage.

Update the authoritative reference first, then related examples. Update the
affected cloud or HomeKit tracker with implementation changes and cite relevant tests. Keep hardware
limits separate from fixture coverage. Do not turn HTTP acceptance or a generic
raw write into semantic support. Release notes stay historical.

Use synthetic identifiers and placeholders for credentials in public examples.
Link directly to the maintained references and update incoming links when pages
move. Keep validation limits beside the relevant protocol findings.
