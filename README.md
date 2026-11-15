# Mysa Local for Home Assistant

[![HACS Default](https://img.shields.io/badge/HACS-Default-41BDF5.svg)](https://www.hacs.xyz/)
[![Validate](https://github.com/SokolskyNikita/mysa-homeassistant/actions/workflows/validate.yml/badge.svg)](https://github.com/SokolskyNikita/mysa-homeassistant/actions/workflows/validate.yml)
[![Tests](https://github.com/SokolskyNikita/mysa-homeassistant/actions/workflows/tests.yml/badge.svg)](https://github.com/SokolskyNikita/mysa-homeassistant/actions/workflows/tests.yml)

> Unofficial and unaffiliated with Mysa. Use at your own risk.

Control Mysa thermostats over your local network through HomeKit. Setpoint, mode, room temperature and heating state work without a Mysa account or internet connection. Setup uses Home Assistant's UI.

An optional Mysa sign-in adds settings, schedules, firmware checks and cloud control when the local connection fails. When cloud access is enabled, MQTT can supply live humidity and numeric heating duty-cycle readings. The integration and per-thermostat MQTT options are on by default; see the [MQTT guide](docs/mqtt.md) for device selection, fallback behavior and validation limits.

Powered by [`mysa-client`](https://github.com/SokolskyNikita/mysa-client). See [architecture](docs/architecture.md) for library and integration ownership.

## Supported models

The maintainer has tested only the Baseboard Lite. Other models rely on owners' reports or on Mysa's app. [Model compatibility](docs/compatibility.md) has what was confirmed, on which firmware, and what is known not to work.

| Model | Model IDs | Status |
|---|---|---|
| Baseboard Lite | `BB-V2-0-L` | Hardware-tested |
| Baseboard V2 | `BB-V2-*` | Reported working; [°F setpoint fix](docs/compatibility.md#baseboard-v2) unconfirmed |
| Baseboard V1 | `BB-V1-*` | [Pairing fails on some units](docs/compatibility.md#baseboard-v1) |
| In-Floor | `INF-V1-0`, `INF-V1` | [Reported working](docs/compatibility.md#in-floor) |
| Mini-Split AC | `AC-V1-0`, `AC-V1` | Untested |
| Central HVAC | `ST-V1-0`, `ST-V1`, `LV` | Untested; control through Mysa's cloud only |
| Baseboard V3 | `BB-V3-0` | [Readings reported working](docs/compatibility.md#baseboard-v3); control through Mysa's cloud only |
| Climate Sensor | `CS-V1-0`, `CS-V1` | Untested; readings through Mysa's cloud only |

The experimental models have no local control: they are set up in the Mysa app, then imported here from your Mysa account. See [experimental models](docs/compatibility.md#experimental-models).

Own a model that is untested? A report helps even when everything works: see [debug and report a problem](docs/guides/debugging.md).

## Installation

Requires Home Assistant 2026.9 or newer.

With Marketplace (built into Home Assistant from 2026.11) or HACS (earlier versions):

1. Open Marketplace (or HACS) and search for **Mysa Local**.
2. Download it and restart Home Assistant.

For a manual installation, copy [custom_components/mysa_local](custom_components/mysa_local) into your Home Assistant `config/custom_components/` directory, then restart.

After restarting, follow the [setup guide](docs/guides/setup.md).

## Documentation

- [Pair, migrate, import, or recover a thermostat](docs/guides/setup.md)
- [Model compatibility: what works on which model](docs/compatibility.md)
- [Entities, options, credentials, and account management](docs/guides/usage.md)
- [Schedules and holds with action examples](docs/guides/schedules.md)
- [Local control and cloud reading freshness](docs/guides/behavior.md)
- [Enable MQTT and understand fallback](docs/mqtt.md)
- [Troubleshoot common problems](docs/guides/troubleshooting.md)
- [Debug and report a problem, or test an untested model](docs/guides/debugging.md)
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

Thanks to everyone who has contributed fixes, reports and testing: @mcrutherford, @lionello, @noahtallen, @giesler, @mscholtz, @barakthecat, @rkoak, @neotrantor and @dlenski.

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
