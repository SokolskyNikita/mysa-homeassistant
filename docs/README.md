# Mysa Local documentation

## Find a task

| I want to… | Start here |
| --- | --- |
| Check what works on your model | [Model compatibility](compatibility.md) |
| Install the integration | [README](../README.md#installation) |
| Pair locally, migrate HomeKit Device, or import an account thermostat | [Setup and recovery](guides/setup.md) |
| Remember or delete HomeKit setup codes | [Remembering setup codes](guides/setup.md#remembering-setup-codes) |
| Configure entities, polling, credentials, names, or account membership | [Usage and options](guides/usage.md) |
| Create a schedule, hold a temperature, or resume scheduling | [Schedules and holds](guides/schedules.md) |
| Understand unavailable readings or cloud fallback | [Reading freshness](guides/behavior.md) and [troubleshooting](guides/troubleshooting.md) |
| Enable live humidity and heating duty readings | [MQTT telemetry](mqtt.md) |
| Test a model nobody has reported on | [Debug and report a problem](guides/debugging.md#test-a-model-nobody-has-reported-on) |
| Capture logs and report a problem | [Debug and report a problem](guides/debugging.md) |
| Compare Mysa Local with other open-source Mysa projects | [Other Mysa clients](other-clients.md) |

## Home Assistant behavior

| Maintainer reference | Owns |
| --- | --- |
| [Development](development.md) | Environment, scripts, and CI |
| [Architecture](architecture.md) | Client/HA ownership, adapters, lifecycle, and storage |
| [Account and schedule adapters](cloud-workflows.md) | HA workflow orchestration and recovery |
| [Feature scope](cloud-scope.md) | Product decisions, exclusions, and transport boundaries |
| [Management inventory](implementation/progress.md) | Current recorded status, acceptance criteria, evidence, and validation gaps |
| [Documentation maintenance](maintenance.md) | Ownership and checks |
| [Release history](../CHANGELOG.md) | Versioned changes |

## Mysa API and protocol references

The [client documentation index](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/README.md)
owns callable Python APIs, protocol formats, reusable Mysa behavior, and protocol
evidence. Start there for standalone use. HA configuration and user workflows
remain in the guides above; [architecture](architecture.md) explains the boundary.
Older protocol URLs here remain forwarding pages for existing bookmarks.

## Evidence limits

See the management inventory's [research and hardware limits](implementation/progress.md#research-and-hardware-limits).
App-derived mappings, automated tests, accepted API requests, and physical
hardware confirmation are different claims. Keep the relevant limits next to
any model or feature claim.
