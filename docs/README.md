# Mysa Local documentation

## Find a task

| I want to… | Start here |
| --- | --- |
| Check model support or install the integration | [README](../README.md#supported-models) |
| Pair locally, migrate HomeKit Device, or import an account thermostat | [Setup and recovery](guides/setup.md) |
| Remember or delete HomeKit setup codes | [Remembering setup codes](guides/setup.md#remembering-setup-codes) |
| Configure entities, polling, credentials, names, or account membership | [Usage and options](guides/usage.md) |
| Create a schedule, hold a temperature, or resume scheduling | [Schedules and holds](guides/schedules.md) |
| Understand unavailable readings or cloud fallback | [Reading freshness](guides/behavior.md) and [troubleshooting](guides/troubleshooting.md) |
| Enable live humidity and heating duty readings | [MQTT telemetry](mqtt.md) |
| Test V3, Central HVAC, or Climate Sensor support | [Experimental models](new-cloud-models.md) |
| Report a command that did not apply | [Capture a command problem](guides/troubleshooting.md#capture-a-command-problem) |

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
| [Historical client extraction](history/client-extraction.md) | Original migration plan and dated results |

Legacy [cloud IDs](implementation/cloud.md) and [HomeKit IDs](implementation/homekit.md)
are lookup tables for old issue links, not separate status inventories.

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
