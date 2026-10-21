# Mysa Local for Home Assistant

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://www.hacs.xyz/docs/faq/custom_repositories/)
[![Validate](https://github.com/SokolskyNikita/mysa-homeassistant/actions/workflows/validate.yml/badge.svg)](https://github.com/SokolskyNikita/mysa-homeassistant/actions/workflows/validate.yml)
[![Tests](https://github.com/SokolskyNikita/mysa-homeassistant/actions/workflows/tests.yml/badge.svg)](https://github.com/SokolskyNikita/mysa-homeassistant/actions/workflows/tests.yml)

> Unofficial and unaffiliated with Mysa. Use at your own risk.

Control Mysa thermostats over your local network through HomeKit. Setpoint, mode, room temperature and heating state work without a Mysa account or internet connection. Setup uses Home Assistant's UI.

An optional Mysa sign-in adds settings, schedules, firmware checks and cloud control when the local connection fails. When cloud access is enabled, MQTT can supply live humidity and numeric heating duty-cycle readings. The integration and per-thermostat MQTT options are on by default; see the [MQTT guide](docs/mqtt.md) for device selection, fallback behavior and validation limits.

Powered by [`mysa-client`](https://github.com/SokolskyNikita/mysa-client). See [architecture](docs/architecture.md) for library and integration ownership.

## Supported models

Only the Baseboard Lite has been tested on hardware. Other models are supported in code with the limits below; their controls still need hardware testing.

| Model | Model IDs | Status |
|---|---|---|
| Baseboard Lite | `BB-V2-0-L` | Hardware-tested on firmware 3.17.5.13; cloud setpoint/mode application differed between the two tested units |
| Other Baseboard V1 / V2 models | `BB-V1-*`, `BB-V2-*` | Supported in code; untested |
| In-Floor | `INF-V1-0` | Supported in code; untested |
| Mini-Split AC | `AC-V1-0` | Supported in code; untested |
| Central HVAC | `ST-V1-0`, `ST-V1`, `LV` | Experimental cloud support after setup in the Mysa app |
| Baseboard V3 | `BB-V3-0` | Experimental cloud support after setup in the Mysa app |
| Climate Sensor | `CS-V1-0`, `CS-V1` | Experimental cloud readings; no climate entity |

For the experimental models, finish setup in the Mysa app, then sign in here and import them from your account. V3 provides basic heat/off and setpoint control. Central HVAC adds heat/cool auto targets and supported fan controls. Climate Sensor provides readings only. Controls depend on the returned capabilities. Schedules/holds, energy/runtime, sensor linking and newer-model OTA are deferred. See [experimental cloud support and the V3 test guide](docs/new-cloud-models.md).

AC fan and swing controls were implemented from Mysa app 4.20.1 and tested with simulated responses. They show the remote's options for the current mode and are hidden while the AC is off. Toggle-only swing, AC auto mode and AC heating/cooling target ranges are not implemented. In-Floor's floor-sensor reading is not exposed.

If you test another model, [submit a model report](https://github.com/SokolskyNikita/mysa-homeassistant/issues/new?template=model_report.yml) with its firmware version and integration diagnostics.

## Installation

Requires Home Assistant 2026.9 or newer.

With HACS:

1. Open HACS and search for **Mysa Local**.
2. Download it and restart Home Assistant.

If Mysa Local isn't listed yet, first add `https://github.com/SokolskyNikita/mysa-homeassistant` under HACS → menu → Custom repositories, with type Integration.

For a manual installation, copy [custom_components/mysa_local](custom_components/mysa_local) into your Home Assistant `config/custom_components/` directory, then restart.

After restarting, follow the [setup guide](docs/guides/setup.md).

## Documentation

- [Pair, migrate, import, or recover a thermostat](docs/guides/setup.md)
- [Entities, options, credentials, and account management](docs/guides/usage.md)
- [Schedules and holds with action examples](docs/guides/schedules.md)
- [Local control and cloud reading freshness](docs/guides/behavior.md)
- [Enable MQTT and understand fallback](docs/mqtt.md)
- [Troubleshoot and capture a command problem](docs/guides/troubleshooting.md)
- [All documentation and maintainer references](docs/README.md)

## Other integration options

| Option | Control path | Requirements |
|---|---|---|
| Mysa Local | HomeKit on the LAN, with optional HTTPS cloud features and fallback | HomeKit code for local pairing; Mysa account for cloud features |
| [HomeKit Device](https://www.home-assistant.io/integrations/homekit_controller/) | Local HomeKit; available controls depend on the services the thermostat exposes | Built into HA; HomeKit pairing |
| [Mysa_HA](https://github.com/kgelinas/Mysa_HA) | Mysa's cloud through MQTT and HTTP | Custom HA integration, Mysa credentials and internet access |
| [mysa2mqtt](https://github.com/bourquep/mysa2mqtt/tree/main/packages/mysa2mqtt) | Mysa's cloud through a bridge to your MQTT broker and HA | Node.js or Docker, an MQTT broker, Mysa credentials and internet access |

Check each project's model and feature list before switching. Mysa Local's hardware testing is limited to the Lite; other integrations may expose controls it does not, including In-Floor sensor data.

## Credits and license

Mysa Local uses [aiohomekit](https://github.com/Jc2k/aiohomekit) for HomeKit. [Mysa_HA](https://github.com/kgelinas/Mysa_HA) and [mysotherm](https://github.com/dlenski/mysotherm) supplied earlier work on Mysa's cloud protocols.

Released under the [MIT License](LICENSE).

<details>
<summary>Previous user-guide section bookmarks</summary>

| Previous section | Maintained guide |
| --- | --- |
| <a id="add-thermostats-from-your-mysa-account"></a>Add thermostats from your Mysa account | [Open guide](docs/guides/setup.md#add-thermostats-from-your-mysa-account) |
| <a id="capture-a-command-problem"></a>Capture a command problem | [Open guide](docs/guides/troubleshooting.md#capture-a-command-problem) |
| <a id="development"></a>Development | [Open guide](docs/development.md#development) |
| <a id="entities-and-controls"></a>Entities and controls | [Open guide](docs/guides/usage.md#entities-and-controls) |
| <a id="integration-options"></a>Integration options | [Open guide](docs/guides/usage.md#integration-options) |
| <a id="local-and-cloud-behavior"></a>Local and cloud behavior | [Open guide](docs/guides/behavior.md#local-and-cloud-behavior) |
| <a id="move-from-homekit-device"></a>Move from HomeKit Device | [Open guide](docs/guides/setup.md#move-from-homekit-device) |
| <a id="options-and-account-management"></a>Options and account management | [Open guide](docs/guides/usage.md#options-and-account-management) |
| <a id="pair-over-your-local-network"></a>Pair over your local network | [Open guide](docs/guides/setup.md#pair-over-your-local-network) |
| <a id="per-thermostat-settings"></a>Per-thermostat settings | [Open guide](docs/guides/usage.md#per-thermostat-settings) |
| <a id="privacy"></a>Privacy | [Open guide](docs/guides/usage.md#privacy) |
| <a id="runtime-and-energy"></a>Runtime and energy | [Open guide](docs/guides/usage.md#runtime-and-energy) |
| <a id="saved-sign-in"></a>Saved sign-in | [Open guide](docs/guides/usage.md#saved-sign-in) |
| <a id="schedules-and-holds"></a>Schedules and holds | [Open guide](docs/guides/schedules.md#schedules-and-holds) |
| <a id="setup"></a>Setup | [Open guide](docs/guides/setup.md#setup) |
| <a id="troubleshooting"></a>Troubleshooting | [Open guide](docs/guides/troubleshooting.md) |

</details>
