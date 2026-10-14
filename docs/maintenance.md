# Documentation maintenance

This repository owns Home Assistant setup and behavior. Mysa API and protocol
references are maintained in [`mysa-client`](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/README.md).

## Ownership

| Material | Maintained home |
| --- | --- |
| Overview, installation, and supported models | [README](../README.md) |
| Pairing, migration, saved codes, and recovery | [Setup guide](guides/setup.md) |
| Entities, options, account controls, runtime, energy, and privacy | [Usage guide](guides/usage.md) |
| Schedule/hold actions and YAML examples | [Schedule guide](guides/schedules.md) |
| Local/cloud source policy and HTTPS freshness | [Behavior guide](guides/behavior.md) |
| Troubleshooting and diagnostic capture | [Troubleshooting guide](guides/troubleshooting.md) |
| Experimental model controls and hardware test procedure | [Newer-model guide](new-cloud-models.md) |
| HA lifecycle, poll cadence, user choices, durable-storage adapters, presentation, and translated errors | [Architecture](architecture.md) and [account/schedule workflows](cloud-workflows.md) |
| Mysa-specific validation, commands, state reconciliation, confirmation, reusable routing, telemetry freshness, and workflow correctness | [Client runtime guide](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/reference/python/runtime.md) and [client cloud guide](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/reference/python/cloud.md) |
| MQTT controls, session lifecycle, HA source preferences, fallback, and hardware evidence | [MQTT guide](mqtt.md) |
| Current recorded HA status, acceptance criteria, and validation gaps | [Management inventory](implementation/progress.md) |
| Product exclusions, scope decisions, and transport boundaries | [Feature scope](cloud-scope.md) |
| Legacy C-* and HK-* bookmarks | [Cloud ID lookup](implementation/cloud.md) and [HomeKit ID lookup](implementation/homekit.md); link new references directly to their destinations |
| Historical extraction design | [Client extraction record](history/client-extraction.md) |
| Public client APIs, MQTT authorization and wire protocol, endpoint/payload details, firmware and provisioning research | [Client protocol index](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/README.md) |
| Raw API method coverage | [Client API inventory](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/api-coverage.md) |
| Sanitized cloud/MQTT exchanges and protocol tools | [Client examples](https://github.com/SokolskyNikita/mysa-client/blob/main/examples/README.md) and [tools](https://github.com/SokolskyNikita/mysa-client/tree/main/tools) |

Update protocol and reusable Mysa-domain behavior in the client repository,
then link to it from HA workflow or acceptance criteria where needed. Keep HA
configuration, source-order choices, polling, storage, lifecycle, presentation,
and hardware evidence in this repository. Do not copy wire or domain rules into
the integration guide. Short forwarding links preserve previously published
protocol bookmarks. Release notes remain historical.

## Issue format

Use the issue form's five sections—Summary, Scope, Acceptance criteria,
Validation, and References—for every issue. Keep observed evidence and
implementation scope distinct, and make acceptance criteria observable. Link
dependencies and the stable source tracker ID. Put each task in its owning
repository: HA lifecycle, entities, options, storage adapters, and presentation
here; shared Mysa commands, state semantics, routing correctness, and reusable
workflows in the [client repository](https://github.com/SokolskyNikita/mysa-client/issues).
Cross-link shared work instead of creating duplicate tasks.

## Evidence and checks

Keep app-derived behavior, simulated tests, and hardware validation distinct.
The strongest live evidence is Baseboard Lite `BB-V2-0-L`, firmware `3.17.5.13`.
An accepted request or stored record does not establish fresh device state or
physical application. Preserve observation limits and cite relevant tests.
Never publish credentials, pairing keys, private identifiers, or raw captures.

Run from the repository root:

```bash
python3 tools/check_docs.py
python3 -m unittest discover -s tools -p 'test_*.py'
python3 tools/update_api_progress.py --check
git diff --check
```

The checks cover all root reader Markdown (including forwarding pages), `docs/`,
and Markdown under `custom_components/`. Dotfiles such as `.impeccable.md` are
editor context and excluded. Relative links and same-repository GitHub `main`
URLs are checked for targets and anchors; directory links resolve their README.
Maintained pages must be reachable from both the root README and docs index.
Only pages marked `<!-- compatibility-forwarder -->` may be unreachable; keep
those pages limited to directing old bookmarks to current content.

`tools/docs_anchors.json` records pre-reorganization page/section contracts.
Do not remove entries to make a failed move pass. Keep an old anchor with a
link to its maintained destination. Add new public bookmarks when reorganizing
newly published pages. The inventory's integration version must match the
manifest; review it on releases without relabeling old hardware evidence as new.
Its status-assessment date remains separate from documentation maintenance.

Checks also cover Markdown tables/fences, legacy tracker IDs, strict JSON
fixtures, and obvious secret patterns. Regression tests run offline with only
the Python standard library; they do not import HA or contact any device.
Semantic claims and the relevance of evidence still need human review.

For coordinated client/HA work, optionally validate client `main` URLs against
an explicit local checkout:

```bash
python3 tools/check_docs.py --client-root ../mysa-client
```

This checks local targets, not remote publication or compatibility with every
released client version. Default CI remains independent of a sibling checkout.
Current links target the published client layout directly. For future coordinated
moves, publish the client destinations before updating HA links, then run this
paired check. Preserve old URLs in historical records. The client checker independently validates its examples.

When HA behavior coverage changes, update the
[management inventory](implementation/progress.md) and run
`python3 tools/update_api_progress.py`. Its `--check` mode verifies the generated
Markdown summary and SVG. The client's raw API map has an independent generator
and denominator; neither score is a bug-free percentage or a hardware guarantee.
