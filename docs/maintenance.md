# Documentation maintenance

This repository owns Home Assistant setup and behavior. Mysa API and protocol
references are maintained in [`mysa-client`](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/README.md).

## Ownership

| Material | Maintained home |
| --- | --- |
| Installation, entities, services, and configuration | [User guide](../README.md) and [newer-model setup/test guide](new-cloud-models.md) |
| HA lifecycle, polling, routing, freshness, persistence, and recovery | [Architecture](architecture.md) and [account/schedule workflows](cloud-workflows.md) |
| MQTT controls, value selection, fallback, and hardware evidence in HA | [MQTT guide](mqtt.md) |
| HA cloud/HomeKit work, acceptance criteria, and product exclusions | [Cloud tracker](implementation/cloud.md) and [HomeKit tracker](implementation/homekit.md) |
| Integration and management coverage | [Management inventory](implementation/progress.md) |
| Public client APIs, MQTT authorization and wire protocol, endpoint/payload details, firmware and provisioning research | [Client protocol index](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/README.md) |
| Raw API method coverage | [Client API inventory](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/api-coverage.md) |
| Sanitized cloud/MQTT exchanges and protocol tools | [Client examples](https://github.com/SokolskyNikita/mysa-client/blob/main/examples/README.md) and [tools](https://github.com/SokolskyNikita/mysa-client/tree/main/tools) |

Update protocol findings in the client repository, then link to them from HA
workflow or acceptance criteria where needed. Keep HA configuration, source
selection, fallback, and hardware evidence in the HA MQTT guide; do not copy the
wire reference here. Short forwarding links preserve previously published
protocol bookmarks. Release notes remain historical.

## Issue format

Use the issue form's five sections—Summary, Scope, Acceptance criteria,
Validation, and References—for every issue. Keep observed evidence and
implementation scope distinct, and make acceptance criteria observable. Link
dependencies and the stable source tracker ID. Put each task in its owning
repository: HA lifecycle, entities, and routing here; client API/protocol work
in the [client repository](https://github.com/SokolskyNikita/mysa-client/issues).
Cross-link shared work instead of creating duplicate tasks.

## Evidence and checks

Keep app-derived behavior, simulated tests, and hardware validation distinct.
The strongest live evidence is Baseboard Lite `BB-V2-0-L`, firmware `3.17.5.13`.
An accepted request or stored record does not establish fresh device state or
physical application. Preserve observation limits and cite relevant tests.
Never publish credentials, pairing keys, private identifiers, or raw captures.

Run `python3 tools/check_docs.py` and `git diff --check`. Checks cover local
Markdown links/anchors, tables and fences, tracker IDs, strict integration
fixtures, and obvious secret patterns. They never import an integration or
contact a device. External links and semantic accuracy need separate review.
The client's own checker validates its protocol examples and references.

When HA behavior coverage changes, update the
[management inventory](implementation/progress.md) and run
`python3 tools/update_api_progress.py`. Its `--check` mode verifies the generated
Markdown summary and SVG. The client's raw API map has an independent generator
and denominator; neither score is a bug-free percentage or a hardware guarantee.
