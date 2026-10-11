# Mysa Local for Home Assistant

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz/docs/faq/custom_repositories)
[![Validate](https://github.com/SokolskyNikita/mysa-homeassistant/actions/workflows/validate.yml/badge.svg)](https://github.com/SokolskyNikita/mysa-homeassistant/actions/workflows/validate.yml)
[![Tests](https://github.com/SokolskyNikita/mysa-homeassistant/actions/workflows/tests.yml/badge.svg)](https://github.com/SokolskyNikita/mysa-homeassistant/actions/workflows/tests.yml)

Control **Mysa smart thermostats** from Home Assistant **over your own network**, with Mysa's cloud as an optional extra.

- Setpoint, mode, room temperature and the real heating state go over HomeKit on your LAN. A change reaches the thermostat in about 0.2 s, and none of it needs the internet.
- Signing in to your Mysa account is optional. It adds what HomeKit doesn't expose: button lock, display brightness, setpoint limits, temperature offset, heater type, humidity, schedules and holds, firmware updates and the thermostat's own heating runtime. The cloud also takes over while the local connection is down.
- Turn the cloud off and the integration makes zero calls to Mysa's servers.
- Setup happens in the UI, with no YAML, and new thermostats are discovered automatically.

> [!IMPORTANT]
> **So far it's only been tested on the Mysa Baseboard Lite (BB-V2-0-L).** The other models marked ✅ under [Supported models](#supported-models) should work through Mysa's own capability data. If you try one, please [send a model report](https://github.com/SokolskyNikita/mysa-homeassistant/issues/new?template=model_report.yml) with your diagnostics.

Not affiliated with or endorsed by Mysa (Empowered Homes Inc.). Mysa is a trademark of its owner.

## Installation

With HACS: HACS → ⋮ → Custom repositories → add `https://github.com/SokolskyNikita/mysa-homeassistant` as an **Integration** → install **Mysa Local** → restart Home Assistant.

Without HACS: copy `custom_components/mysa_local` into your `config/custom_components/` and restart.

Requires Home Assistant 2026.9 or newer.

## Setup

1. If the thermostat isn't on Wi-Fi yet (a new or reset Mysa shows a Bluetooth icon), put it on your 2.4 GHz Wi-Fi with the [Wi-Fi setup page](https://sokolskynikita.github.io/mysa-wifi-setup/). It runs in Chrome or Edge on a computer with Bluetooth and doesn't need the Mysa app or a phone.
2. Press a button on the thermostat to wake it: an unpaired Mysa stops answering HomeKit after a while. Then click **Add** on it in Home Assistant and enter the 8-digit HomeKit code from the back of the faceplate (spaces and dashes are fine). A thermostat can only be paired to one HomeKit controller, so remove it from Apple Home first if it's there.
3. Sign in to your Mysa account if you want the cloud features, or skip this and sign in later from the options. By default Home Assistant keeps only a sign-in token, which seems to last about a month; after that it asks you to sign in again. Tick **Save my password** to have Mysa Local sign in again by itself instead.

To add more thermostats, let Home Assistant discover them or use **Add thermostat** on the integration's page. A thermostat that can't be reached locally can be added from your Mysa account, with cloud control only.

### Mysa account and local pairing

While you're signed in, Mysa Local keeps the two sides matched:
- Pairing a thermostat that isn't on your Mysa account offers to add it there. A thermostat that was on the account before keeps its stored settings, such as heater type and display units.
- Adding a thermostat from your Mysa account offers to pair it locally too. Mysa Local finds it on your network by its name (`Mysa-` plus the end of its Wi-Fi MAC) and asks for the HomeKit code.
- A paired thermostat that isn't on the account, or one that's only connected through the cloud, shows up under **Settings → Repairs** as a warning with a fix. Ignore it if that's how you want it.

### Moving from the HomeKit Device integration

Choose **Move a thermostat from the HomeKit Device integration**. Mysa Local adds its own HomeKit pairing through the existing one, so no setup code is needed. It keeps your entity IDs, names and areas, then removes the HomeKit Device entry.

## Supported models

| Model | Code | Local (HomeKit) | Cloud | Status |
|---|---|---|---|---|
| Mysa Baseboard Lite | `BB-V2-0-L` | ✅ | ✅ | Tested (firmware 3.17.5.13) |
| Mysa Baseboard V1 / V2 | `BB-V1-x`, `BB-V2-0` | ✅ | ✅ | Untested |
| Mysa In-Floor | `INF-V1-0` | ✅ | ✅ | Untested |
| Mysa for Mini-Split AC | `AC-V1-0` | ✅ | ✅ | Untested |
| Mysa for Central HVAC | `ST-V1-0` | ❌ | ❌ | Not supported |
| Mysa Baseboard V3 | `BB-V3-0` | ❌ | ❌ | Not supported |
| Mysa Climate Sensor | `CS-V1-0` | ❌ | ❌ | Not supported |

To finish setup, the Baseboard V3, Central HVAC and Climate Sensor need a credential file from Mysa's servers sent over Bluetooth, which Home Assistant can't do. Mysa Local leaves them out of discovery and doesn't list them when adding from your Mysa account. Set them up in the Mysa app and add them to Home Assistant's [Matter integration](https://www.home-assistant.io/integrations/matter/) instead.

## Core entities

| Entity | Source | Notes |
|---|---|---|
| Climate | Local, cloud fallback | Setpoint, mode, room temperature and the real heating state. On a Mini-Split AC, also fan speed and vertical and horizontal swing (cloud), with the options its remote has in the current mode. |
| Temperature | Local, cloud fallback | |
| Humidity | Cloud | |
| Heating runtime today | Local or cloud | Resets at midnight. |
| Heater power and energy | Derived | From the heater wattage you enter. Energy works in the Energy dashboard. |
| Button lock | Cloud | |
| Wake / sleep brightness | Cloud | |
| Lowest / highest setpoint | Cloud | The climate entity follows them. |
| Temperature offset | Cloud | |
| Heater type | Cloud | Baseboard or fan forced. |
| Firmware update | Cloud | Installing is off unless you allow it (see [Options](#options)). |
| Connection path | — | `local`, `cloud` or `offline`. |

## Options

The integration's options have the cloud switch, the cloud poll interval (30–300 s, default 60), **Live mode during local outages** (off by default; see [How local-first works](#how-local-first-works)) and Mysa sign-in or sign-out. With the cloud off, the cloud-only entities are disabled but keep their names, areas and history; turning it back on brings them back. Under *Advanced* there's **Allow firmware installs**, off by default: a firmware install is untested with this integration and can't be undone.

The options page also says which Mysa account is signed in and whether its password is saved. To change the save-password choice, sign out and sign in again. Signing out deletes a saved password.

**Thermostats on my Mysa account**, also in the options, is a view-only table of every thermostat on the account: its name in Home Assistant and in the Mysa app, and whether it's in Home Assistant (local and cloud, cloud only or not added).

Each thermostat's **Settings and status** (⋮ → Settings and status) shows whether it's on your Mysa account, its name there and whether it's paired locally. From there you can:
- set the heater wattage (adds the power and energy sensors) and choose whether to use the thermostat's own measured runtime, under **Settings**
- **Pair locally** with the HomeKit code, for a thermostat added from the account
- **Add to** or **Remove from my Mysa account**. This is the only place a thermostat is removed from the account. Removing first takes it out of the account's Home/Away/Vacation shortcuts and deletes its Mysa schedule. Mysa keeps its settings, so adding it back restores them.
- use the Mysa app's name in Home Assistant, or rename it in the Mysa app to match, when the two names differ

## Actions

| Action | What it does |
|---|---|
| `mysa_local.hold` | Holds a setpoint (and optionally a mode) over the Mysa schedule, permanently or `until` a time. Applied right away. Needs a Mysa schedule on the thermostat (Mysa has nothing to hold over otherwise). |
| `mysa_local.resume_schedule` | Ends the hold. |
| `mysa_local.set_schedule` | Replaces the weekly schedule stored by Mysa. It keeps running even when Home Assistant or the internet is down. |
| `mysa_local.clear_schedule` | Deletes it. |

```yaml
action: mysa_local.set_schedule
target:
  entity_id: climate.living_room
data:
  events:
    - days: [mon, tue, wed, thu, fri]
      time: "06:30"
      temperature: 21
    - days: [mon, tue, wed, thu, fri]
      time: "22:00"
      temperature: 18
```

These need the cloud. If you'd rather keep schedules in Home Assistant, plain automations on the climate entity work fully locally.

## How local-first works

- HomeKit pushes every change, so the only local polling is a light health check every 30 s.
- The cloud is read with **one request for all thermostats** every 60 s (configurable). While the local connection is healthy, cloud values for the same fields are ignored, so the two never fight.
- A change goes to HomeKit first, with a 5 s timeout. If HomeKit is down or fails, the same change goes through the cloud. After a local write times out, the integration reads the value back before trying again, so a change is never applied twice.
- A thermostat on the cloud is read at the normal poll interval, so its readings can be a few minutes old. With **Live mode during local outages** on, Mysa is asked for a fresh reading every ~5 s and the cloud is read every 10 s instead, until the local connection is back.
- The **Connection path** sensor shows which path is active. If the local connection stays down for 15 minutes, a repair issue lists what to check (Wi-Fi, a DHCP reservation, power). It clears itself.
- The integration backs off on rate limits and server errors and spaces out its writes.

## Privacy

- Mysa Local stores the thermostats' HomeKit pairing keys and, if you signed in, your Mysa email and a sign-in token. Your password is stored only if you ticked **Save my password**, unencrypted in Home Assistant's configuration. The sign-in uses SRP, so the password itself is never sent to Mysa.
- With the cloud off (or never signed in), nothing is sent to Mysa.
- Diagnostics redact tokens, keys, addresses, serial numbers and account IDs.

## Troubleshooting

- If a thermostat isn't discovered, or pairing says it couldn't reach the thermostat's HomeKit service, press a button on the thermostat to wake it. Otherwise check that it's on Wi-Fi, on the same network as Home Assistant (mDNS has to work between them) and not paired to Apple Home.
- If it keeps dropping to `cloud`, give the thermostat a DHCP reservation and check its Wi-Fi signal (enable the *Wi-Fi signal* sensor).
- Deleting a thermostat (or the integration) removes Mysa Local's HomeKit pairing from it. If the thermostat was offline at the time, reset its HomeKit pairing on the device before pairing again.
- Debug logging can be turned on from the integration's page.
- Settings → System → Repairs → ⋮ → System information has a Mysa Local section: how many thermostats are on the local path, on the cloud or offline, whether you're signed in, whether the password is saved and whether Mysa's server can be reached.

## Development

```bash
scripts/setup    # install HA, test and lint dependencies (Python 3.14)
scripts/test     # pytest with coverage
scripts/lint     # ruff format, ruff check, mypy --strict
scripts/develop  # run a development Home Assistant with the integration
```

The cloud client in `custom_components/mysa_local/api/` has no Home Assistant imports, so it can become its own library. The API notes are in [`docs/cloud-api.md`](docs/cloud-api.md).

## Compared with other options

| | **Mysa Local** | [HomeKit Device](https://www.home-assistant.io/integrations/homekit_controller/) | [Mysa_HA](https://github.com/kgelinas/Mysa_HA) | [mysa2mqtt](https://github.com/bourquep/mysa2mqtt) |
|---|---|---|---|---|
| Runs as | Integration from HACS, set up in the UI | Built into Home Assistant | Integration from HACS, set up in the UI | A separate Node.js program or Docker container, plus an MQTT broker |
| Works without the internet or Mysa's cloud | ✅ | ✅ | ❌ | ❌ |
| Falls back to the cloud when the local connection drops | ✅ | ❌ | — | — |
| Setpoint and mode changes go over | HomeKit, else Mysa's HTTPS API | HomeKit | Mysa's MQTT broker | Mysa's MQTT broker |
| Mysa password stored | Only if you choose to (otherwise a sign-in token) | — | Yes | Yes, in its configuration |
| Setpoint, mode, room temperature | ✅ | ✅ | ✅ | ✅ |
| Real heating state (relay on/off) | ✅ | ✅ | Estimated from temperature vs setpoint | ✅ |
| Humidity | ✅ | ❌ | ✅ | ✅ |
| °C/°F on the thermostat's screen | ✅ | ✅ | ✅ | ❌ |
| Button lock | ✅ | ❌ | ✅ | ❌ |
| Display brightness | ✅ | ❌ | ✅ | ❌ |
| Hardware setpoint limits | ✅ (the climate entity follows them) | ❌ (always 5–30 °C) | ⚠️ (can be set, but the climate entity stays at 5–30 °C) | ❌ |
| Temperature offset | ✅ | ❌ | ❌ | ❌ |
| Heater type (how often the relay switches) | ✅ | ❌ | ❌ | ❌ |
| Mysa's weekly schedule | ✅ | ❌ | ❌ ([open PR](https://github.com/kgelinas/Mysa_HA/pull/20)) | ❌ |
| Hold / resume schedule | ✅ | ❌ | ❌ ([open PR](https://github.com/kgelinas/Mysa_HA/pull/20)) | ❌ |
| Firmware | Check, and install if you allow it | ❌ | Check only | ❌ |
| Heating runtime measured by the thermostat | ✅ | ❌ | ❌ | ❌ |
| Power and energy | Estimated from the heater wattage you enter | ❌ | Measured on the Baseboard V1, estimated on others | Measured on the Baseboard V1, estimated on others |
| Identify | ✅ | ✅ | ❌ | ❌ |
| Add or remove a thermostat on the Mysa account | ✅ | ❌ | ❌ | ❌ |
| Mini-split AC fan speed and swing | ✅ | ❌ | ✅ | Fan speed only |
| In-Floor floor sensor | ❌ | ❌ | ✅ | ✅ |
| Models tested | Baseboard Lite | Any Mysa with HomeKit | Baseboard V1/V2/Lite, In-Floor, AC; Central HVAC needs testing | Baseboard V1/V2/Lite, In-Floor, AC |
| Oldest Home Assistant supported | 2026.9 | — | 2024.1 | — |
| First / latest release | Oct 2026 / Oct 2026 | — | Dec 2025 / Mar 2026 | May 2025 / Sep 2026 |

## Credits

Built on [aiohomekit](https://github.com/Jc2k/aiohomekit), the library behind Home Assistant's HomeKit support. Thanks to the community projects that mapped Mysa's protocols first, including [Mysa_HA](https://github.com/kgelinas/Mysa_HA) and [mysotherm](https://github.com/dlenski/mysotherm), a Python command-line tool for Mysa thermostats.

## License

[MIT](LICENSE)
