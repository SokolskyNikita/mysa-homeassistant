# Mysa Local for Home Assistant

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz/docs/faq/custom_repositories)
[![Validate](https://github.com/SokolskyNikita/mysa-homeassistant/actions/workflows/validate.yml/badge.svg)](https://github.com/SokolskyNikita/mysa-homeassistant/actions/workflows/validate.yml)
[![Tests](https://github.com/SokolskyNikita/mysa-homeassistant/actions/workflows/tests.yml/badge.svg)](https://github.com/SokolskyNikita/mysa-homeassistant/actions/workflows/tests.yml)

Control **Mysa smart thermostats** from Home Assistant **over your own network**, with Mysa's cloud as an optional extra.

- **Local first.** Setpoint, mode, room temperature and the real heating state go over HomeKit on your LAN: instant, and they keep working without internet.
- **Cloud optional.** Sign in to your Mysa account to add what HomeKit doesn't expose: button lock, display brightness, setpoint limits, temperature offset, heater type, schedules and holds, firmware updates and the thermostat's own heating runtime. The cloud also takes over while the local connection is down.
- **Cloud off switch.** Turn the cloud off and the integration makes zero calls to Mysa's servers.
- **Easy setup.** HACS, UI setup, no YAML. New thermostats are discovered automatically, and there's a link to a [browser-based Wi-Fi setup page](https://sokolskynikita.github.io/mysa-wifi-setup/) for thermostats that aren't on Wi-Fi yet.

> [!IMPORTANT]
> **Tested on the Mysa Baseboard Lite (BB-V2-0-L), firmware 3.17.5.13.** Other Mysa models are supported through Mysa's own capability data but haven't been tested yet. If you try one, please [send a model report](https://github.com/SokolskyNikita/mysa-homeassistant/issues/new?template=model_report.yml) with your diagnostics.

Not affiliated with or endorsed by Mysa (Empowered Homes Inc.). Mysa is a trademark of its owner.

## Tested models

| Model | Code | Local (HomeKit) | Cloud | Status |
|---|---|---|---|---|
| Mysa Baseboard Lite | `BB-V2-0-L` | ✅ | ✅ | Tested (firmware 3.17.5.13) |
| Mysa Baseboard V1 / V2 | `BB-V1-x`, `BB-V2-0` | expected | expected | Untested |
| Mysa In-Floor | `INF-V1-0` | expected | expected | Untested |
| Mysa for Mini-Split AC | `AC-V1-0` | expected | partial (no fan speed or swing yet) | Untested |
| Mysa for Central HVAC | `ST-V1-0` | expected | partial | Untested |
| Mysa Baseboard V3 | `BB-V3-0` | via Matter later | expected | Untested |

## What you get

| Entity | Source | Notes |
|---|---|---|
| Climate | Local, cloud fallback | Setpoint, mode, room temperature, real relay state (heating/idle). Limits follow the thermostat's hardware setpoint limits. |
| Temperature sensor | Local, cloud fallback | For history and statistics. |
| Identify button | Local | Makes the thermostat signal itself. |
| Display units select | Local, cloud fallback | °C/°F on the thermostat's own screen. HA shows temperatures in your HA units either way. |
| Connection path sensor | — | `local`, `cloud` or `offline`. |
| Heating runtime today | Cloud or local | The thermostat's own measured on-time (cloud), or counted from the heating state (local). Resets at midnight. |
| Heater energy today | Derived | Runtime × the wattage you enter. Works in the Energy dashboard. |
| Button lock switch | Cloud | |
| Wake / sleep brightness | Cloud | |
| Lowest / highest setpoint | Cloud | The thermostat's hardware limits. |
| Temperature offset | Cloud | |
| Heater type select | Cloud | Baseboard or fan forced (how often the relay switches). |
| Firmware update | Cloud | Checks for updates. Installing is off unless you allow it (see Options). |
| Cloud connection | Cloud | Whether Mysa's cloud hears from the thermostat. |
| Wi-Fi signal | Cloud | Disabled by default. |
| Schedule hold | Cloud | On while a hold overrides the Mysa schedule. |

Each model only gets the entities its capability data says it supports. Humidity isn't exposed on the Lite: Mysa's own data says it has no humidity sensor.

## Installation

**HACS (recommended):** HACS → ⋮ → Custom repositories → add `https://github.com/SokolskyNikita/mysa-homeassistant` as an **Integration** → install **Mysa Local** → restart Home Assistant.

**Manual:** copy `custom_components/mysa_local` into your `config/custom_components/` and restart.

Requires Home Assistant 2026.9 or newer.

## Setup

1. **Thermostat not on Wi-Fi yet?** A new or reset Mysa shows a Bluetooth icon. Put it on your 2.4 GHz Wi-Fi with the [Wi-Fi setup page](https://sokolskynikita.github.io/mysa-wifi-setup/) (Chrome or Edge on a computer with Bluetooth; no Mysa app, no phone).
2. **Pair.** Home Assistant discovers the thermostat. Click **Add** and enter the 8-digit HomeKit code from the back of the faceplate. A thermostat can only be paired to one HomeKit controller, so remove it from Apple Home first if it's there.
3. **Mysa sign-in (optional).** Sign in to add the cloud features. Your password is used once to sign in (with SRP, so it isn't even sent to Mysa in plain form) and isn't stored; Home Assistant keeps only a sign-in token. You can skip this and sign in later from the options.

More thermostats: they're discovered too, or use **Add thermostat** on the integration's page. Thermostats without a local path can be added from your Mysa account (cloud control only).

### Already using the HomeKit Device integration?

Choose **Move a thermostat from the HomeKit Device integration**. Mysa Local adds its own HomeKit pairing through the existing one (no setup code needed), keeps your entity IDs, names and areas, and then removes the HomeKit Device entry.

## Options

- **Integration options:** cloud on/off, cloud poll interval (30–300 s, default 60), sign in or out, and under *Advanced*, **Allow firmware installs** (off by default; a firmware install is untested with this integration and can't be undone).
- **Per thermostat (⋮ → Settings):** heater wattage (adds the energy sensor) and whether to use the thermostat's own measured runtime.

## Actions

| Action | What it does |
|---|---|
| `mysa_local.hold` | Holds a setpoint (and optionally a mode) over the Mysa schedule, permanently or `until` a time. |
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

- HomeKit pushes every change, so there's no local polling beyond a light health check every 30 s.
- The cloud is read with **one request for all thermostats** every 60 s (configurable). While the local connection is healthy, cloud values for the same fields are ignored, so the two never fight.
- A change goes to HomeKit first, with a 5 s timeout. If HomeKit is down or fails, the same change goes through the cloud. If a local write times out with an unknown outcome, the integration reads the current value back (locally, then from the cloud) before trying anywhere else, so a change is never applied twice.
- The **Connection path** sensor shows which path is active. If the local connection stays down for 15 minutes, a repair issue explains what to check (Wi-Fi, a DHCP reservation, power). It clears itself.
- Being a good API citizen: polling stays gentle, it backs off on rate limits and server errors, writes are spaced out, and the User-Agent names this integration.

## Privacy

- Stored: the thermostats' HomeKit pairing keys and, if you signed in, your Mysa email and a refresh token. Your password is never stored.
- With the cloud off (or never signed in), nothing is sent to Mysa.
- Diagnostics redact tokens, keys, addresses, serial numbers and account IDs.

## Troubleshooting

- **Not discovered:** the thermostat must be on the same network as Home Assistant, with mDNS working between them. Check it's on Wi-Fi and not paired to Apple Home.
- **Keeps dropping to `cloud`:** give the thermostat a DHCP reservation and check its Wi-Fi signal (enable the *Wi-Fi signal* sensor).
- **Re-pairing:** deleting a thermostat (or the integration) removes Mysa Local's HomeKit pairing from it. If the thermostat was offline at the time, reset its HomeKit pairing on the device before pairing again.
- **Debug logs:** enable debug logging on the integration's page.

## Development

```bash
scripts/setup    # install HA, test and lint dependencies (Python 3.14)
scripts/test     # pytest with coverage
scripts/lint     # ruff format, ruff check, mypy --strict
scripts/develop  # run a development Home Assistant with the integration
```

The cloud client in `custom_components/mysa_local/api/` has no Home Assistant imports, so it can become its own library. The API notes are in [`docs/cloud-api.md`](docs/cloud-api.md) and the design in [`docs/plan.md`](docs/plan.md).

## Credits

Built on [aiohomekit](https://github.com/Jc2k/aiohomekit), the library behind Home Assistant's HomeKit support. Thanks to the community projects that mapped Mysa's protocols first, including [Mysa_HA](https://github.com/kgelinas/Mysa_HA) and [mysotherm](https://github.com/dlenski/mysotherm).

## License

[MIT](LICENSE)
