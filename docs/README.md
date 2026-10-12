# Mysa Local documentation

The [installation and user guide](../README.md) covers setup, supported models,
entities, services, and integration options.

## Home Assistant behavior

| Reference | Contents |
| --- | --- |
| [Architecture](architecture.md) | HA lifecycle, polling, local-first routing, fallback, persistence, and ownership |
| [Account and schedule workflows](cloud-workflows.md) | HA enrollment, removal, schedule management, and recovery policy |
| [Experimental newer-model support](new-cloud-models.md) | HA controls, setup, diagnostics, and the V3 hardware test procedure |
| [Cloud feature scope](cloud-scope.md) | Which cloud features belong in HA and why |
| [Cloud integration tracker](implementation/cloud.md) | HA orchestration, acceptance criteria, product exclusions, and hardware limits |
| [HomeKit integration tracker](implementation/homekit.md) | HA discovery, pairing storage, entities, migration, and recovery |
| [Integration and management coverage](implementation/progress.md) | State selection, command handling, lifecycle, workflows, and verification |
| [Documentation maintenance](maintenance.md) | Ownership boundaries, evidence rules, and offline checks |
| [Release history](../CHANGELOG.md) | Versioned changes and recorded validation |

## Mysa API and protocol references

Protocol documentation is maintained in the standalone
[`mysa-client` documentation](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/README.md).
That includes cloud authentication, endpoint and payload shapes, account/schedule
wire mechanics, HomeKit characteristics, Bluetooth/SoftAP setup, MQTT, firmware,
and Matter research. Sanitized exchanges and protocol diagnostic tools live
there too. The
[raw API inventory](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/api-coverage.md)
tracks client methods independently of this integration's management coverage.

The integration consumes [`mysa_client.cloud`](https://github.com/SokolskyNikita/mysa-client/blob/main/src/mysa_client/cloud/README.md)
and [`mysa_client.homekit`](https://github.com/SokolskyNikita/mysa-client/blob/main/src/mysa_client/homekit/README.md).
The [Wi-Fi setup page](https://github.com/SokolskyNikita/mysa-wifi-setup) remains a
separate product. Older protocol URLs in this repository forward readers to
their maintained references.

## Evidence limits

Live validation is strongest for two Baseboard Lites (`BB-V2-0-L`, firmware
`3.17.5.13`). Other models have app-derived or simulated mappings and need
matching hardware checks. API acceptance, stored records, device reports,
local confirmation, and physical behavior are different claims. Hardware
limits stay beside the relevant integration or protocol findings.
