# Mysa Local for Home Assistant

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz/docs/faq/custom_repositories)
[![Validate](https://github.com/SokolskyNikita/mysa-homeassistant/actions/workflows/validate.yml/badge.svg)](https://github.com/SokolskyNikita/mysa-homeassistant/actions/workflows/validate.yml)
[![Tests](https://github.com/SokolskyNikita/mysa-homeassistant/actions/workflows/tests.yml/badge.svg)](https://github.com/SokolskyNikita/mysa-homeassistant/actions/workflows/tests.yml)

Control **Mysa smart thermostats** from Home Assistant **over your own network**, with Mysa's cloud as an optional extra.

- Setpoint, mode, room temperature and the real heating state go over HomeKit on your LAN. A change reaches the thermostat in about 0.2 s, and none of it needs the internet.
- Signing in to your Mysa account is optional. It adds what HomeKit doesn't expose: button lock, display brightness, setpoint limits, temperature offset, heater type, humidity, schedules and holds, firmware updates and the thermostat's own heating runtime. The cloud also takes over while the local connection is down.
- Turn the cloud off and the integration makes zero calls to Mysa's servers.
- Install it from HACS and set it up in the UI; there's no YAML. New thermostats are discovered automatically, and there's a link to a [browser-based Wi-Fi setup page](https://sokolskynikita.github.io/mysa-wifi-setup/) for thermostats that aren't on Wi-Fi yet.

> [!IMPORTANT]
> **Tested on the Mysa Baseboard Lite (BB-V2-0-L), firmware 3.17.5.13.** The other models marked ✅ below are supported through Mysa's own capability data but haven't been tested yet. If you try one, please [send a model report](https://github.com/SokolskyNikita/mysa-homeassistant/issues/new?template=model_report.yml) with your diagnostics.

Not affiliated with or endorsed by Mysa (Empowered Homes Inc.). Mysa is a trademark of its owner.

## Tested models

| Model | Code | Local (HomeKit) | Cloud | Status |
|---|---|---|---|---|
| Mysa Baseboard Lite | `BB-V2-0-L` | ✅ | ✅ | Tested (firmware 3.17.5.13) |
| Mysa Baseboard V1 / V2 | `BB-V1-x`, `BB-V2-0` | ✅ | ✅ | Untested |
| Mysa In-Floor | `INF-V1-0` | ✅ | ✅ | Untested |
| Mysa for Mini-Split AC | `AC-V1-0` | ✅ | ✅ (no fan speed or swing yet) | Untested |
| Mysa for Central HVAC | `ST-V1-0` | ❌ | ❌ | Unsupported (needs a credential file from Mysa's servers) |
| Mysa Baseboard V3 | `BB-V3-0` | ❌ | ❌ | Unsupported (needs a credential file from Mysa's servers) |
| Mysa Climate Sensor | `CS-V1-0` | ❌ | ❌ | Unsupported (needs a credential file from Mysa's servers) |

## What you get

| Entity | Source | Notes |
|---|---|---|
| Climate | Local, cloud fallback | Setpoint, mode, room temperature, real relay state (heating/idle). Limits follow the thermostat's hardware setpoint limits. |
| Temperature sensor | Local, cloud fallback | For history and statistics. |
| Identify button | Local | Makes the thermostat signal itself. |
| Display units select | Local, cloud fallback | °C/°F on the thermostat's own screen. HA shows temperatures in your HA units either way. |
| Connection path sensor | — | `local`, `cloud` or `offline`. |
| HomeKit pairing sensor | — | `paired`, or `not_paired` for a thermostat that's only connected through the cloud. |
| Mysa account sensor | Cloud | `on_account` or `not_on_account` (only while signed in). |
| Heating runtime today | Cloud or local | The thermostat's own measured on-time (cloud), or counted from the heating state (local). Resets at midnight. |
| Heater power | Derived | The wattage you enter while the heater is on, 0 W while it's off. |
| Heater energy today | Derived | Runtime × the wattage you enter. Works in the Energy dashboard. |
| Humidity sensor | Cloud | Room humidity. Also shown on the climate entity. |
| Button lock switch | Cloud | |
| Wake / sleep brightness | Cloud | Steps of 5, like Mysa's app. Wake is at least 10 %, and sleep is never brighter than wake. |
| Lowest / highest setpoint | Cloud | The thermostat's hardware limits. |
| Temperature offset | Cloud | |
| Heater type select | Cloud | Baseboard or fan forced (how often the relay switches). |
| Firmware update | Cloud | Checks for updates. Installing is off unless you allow it (see Options). |
| Cloud connection | Cloud | Whether Mysa's cloud hears from the thermostat. |
| Wi-Fi signal | Cloud | Disabled by default. |
| Schedule hold | Cloud | On while a hold overrides the Mysa schedule. |

Each model only gets the entities its capability data says it supports. Humidity is the exception: it's added whenever the cloud reports it. Mysa's capability data says the Lite has no humidity sensor, yet the Lite reports a reading that matched a separate sensor in the same room.

## Installation

**HACS (recommended):** HACS → ⋮ → Custom repositories → add `https://github.com/SokolskyNikita/mysa-homeassistant` as an **Integration** → install **Mysa Local** → restart Home Assistant.

**Manual:** copy `custom_components/mysa_local` into your `config/custom_components/` and restart.

Requires Home Assistant 2026.9 or newer.

## Setup

1. **Thermostat not on Wi-Fi yet?** A new or reset Mysa shows a Bluetooth icon. Put it on your 2.4 GHz Wi-Fi with the [Wi-Fi setup page](https://sokolskynikita.github.io/mysa-wifi-setup/) (it runs in Chrome or Edge on a computer with Bluetooth and doesn't need the Mysa app or a phone).
2. **Pair.** Home Assistant discovers the thermostat. Click **Add** and enter the 8-digit HomeKit code from the back of the faceplate (spaces and dashes are fine). **Press a button on the thermostat first:** while a Mysa is unpaired, its HomeKit service dozes off after a while, and pairing can't reach it until it's woken. A thermostat can only be paired to one HomeKit controller, so remove it from Apple Home first if it's there.
3. **Mysa sign-in [optional].** Sign in to add the cloud features. Your password is used to sign in with SRP, so it isn't even sent to Mysa in plain form. By default it isn't stored and Home Assistant keeps only a sign-in token. That token seems to last about a month, and then Home Assistant asks you to sign in again. Tick **Save my password** to have Mysa Local sign in again by itself instead; the password is then stored unencrypted in Home Assistant's configuration, as most integrations store theirs. You can skip all this and sign in later from the options.

More thermostats: they're discovered too, or use **Add thermostat** on the integration's page. Thermostats without a local path can be added from your Mysa account (cloud control only).

**Local and cloud, kept in step.** While you're signed in:
- Pairing a thermostat that isn't on your Mysa account offers to add it there (optional). Mysa Local claims it with its name and your Home Assistant time zone, using the Mysa app's older, smaller claim, so a thermostat that was on the account before keeps its settings (heater type, display units).
- Adding a thermostat from your Mysa account offers to pair it locally too. Mysa Local finds it on your network by its name (`Mysa-` plus the end of its Wi-Fi MAC) and asks for the HomeKit code.
- A paired thermostat that isn't on the account, or one that's only connected through the cloud, shows up under **Settings → Repairs** as a warning with a fix. Ignore it if that's how you want it. Home Assistant has no warning badge on individual devices, so Repairs plus the two diagnostic sensors above is how it shows.

Mysa Local doesn't support the Baseboard V3, Central HVAC or Climate Sensor. To join, they need a credential file from Mysa's servers sent over Bluetooth, which Home Assistant can't do. Discovery leaves them alone, and adding from your Mysa account doesn't list them.

### Already using the HomeKit Device integration?

Choose **Move a thermostat from the HomeKit Device integration**. Mysa Local adds its own HomeKit pairing through the existing one (no setup code needed), keeps your entity IDs, names and areas, and then removes the HomeKit Device entry.

## Options

The integration options have the cloud switch, the cloud poll interval (30–300 s, default 60), **Live mode during local outages** (off by default; see [How local-first works](#how-local-first-works)) and Mysa sign-in or sign-out. With the cloud off, the cloud-only entities are disabled but keep their names, areas and history; turning it back on brings them back. Under *Advanced* there's **Allow firmware installs**, off by default: a firmware install is untested with this integration and can't be undone.

The options page also says which Mysa account is signed in and whether its password is saved. The save-password choice is made at sign-in; to change it, sign out and sign in again. Signing out deletes a saved password too.

**Thermostats on my Mysa account**, also in the integration options, is a view-only table of every thermostat on the account. It has each one's name in Home Assistant and in the Mysa app, and whether it's in Home Assistant (local and cloud, cloud only or not added).

Each thermostat's **Settings and status** (⋮ → Settings and status) shows whether it's on your Mysa account, its name there and whether it's paired locally. From there you can:
- set the heater wattage (adds the power and energy sensors) and choose whether to use the thermostat's own measured runtime, under **Settings**
- **Pair locally** with the HomeKit code, for a thermostat added from the account
- **Add to** or **Remove from my Mysa account**. This is the only place a thermostat is removed from the account. Removing first takes it out of the account's Home/Away/Vacation shortcuts and deletes its Mysa schedule, like the Mysa app does. Mysa keeps its settings, so adding it back restores them.
- use the Mysa app's name in Home Assistant, or rename it in the Mysa app to match, when the two names differ

The device's model line shows the thermostat's Mysa ID (its Wi-Fi MAC, which Mysa's app and API use) and, when it differs, its name in the Mysa app: for example *Mysa Baseboard Lite · Mysa ID a1b2c3d4e5f6 · Mysa app: Bedroom*. Home Assistant's device lists and device page only show a device's name and model, so that's where they fit.

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

- HomeKit pushes every change, so there's no local polling beyond a light health check every 30 s.
- The cloud is read with **one request for all thermostats** every 60 s (configurable). While the local connection is healthy, cloud values for the same fields are ignored, so the two never fight.
- A change goes to HomeKit first, with a 5 s timeout. If HomeKit is down or fails, the same change goes through the cloud. If a local write times out with an unknown outcome, the integration reads the current value back (locally, then from the cloud) before trying anywhere else, so a change is never applied twice.
- **Live mode during local outages** (optional, off by default; in the integration's **Cloud and polling** options): while a thermostat runs on the cloud, the integration asks Mysa for "live mode" (a fresh reading every ~5 s instead of every few minutes) and polls every 10 s. Both stop as soon as the local connection is back, and never run while it's healthy. With it off, a thermostat on the cloud is read at the normal poll interval, and its readings can be a few minutes old.
- The **Connection path** sensor shows which path is active. If the local connection stays down for 15 minutes, a repair issue explains what to check (Wi-Fi, a DHCP reservation, power). It clears itself.
- The integration backs off on rate limits and server errors, spaces out its writes and names itself in the User-Agent.

## Privacy

- Stored: the thermostats' HomeKit pairing keys and, if you signed in, your Mysa email and a refresh token. Your password is stored only if you ticked **Save my password**, unencrypted in Home Assistant's configuration.
- With the cloud off (or never signed in), nothing is sent to Mysa.
- Diagnostics redact tokens, keys, addresses, serial numbers and account IDs.

## Troubleshooting

- **Not discovered, or "Couldn't reach the thermostat's HomeKit service" while pairing:** press a button on the thermostat. An unpaired Mysa stops advertising and answering HomeKit after a while (seen on the Baseboard Lite, firmware 3.17.5.13) and wakes up on a button press. Otherwise check it's on the same network as Home Assistant (mDNS has to work between them), on Wi-Fi and not paired to Apple Home.
- **Keeps dropping to `cloud`:** give the thermostat a DHCP reservation and check its Wi-Fi signal (enable the *Wi-Fi signal* sensor).
- **Re-pairing:** deleting a thermostat (or the integration) removes Mysa Local's HomeKit pairing from it. If the thermostat was offline at the time, reset its HomeKit pairing on the device before pairing again.
- **Debug logs:** enable debug logging on the integration's page.
- For an overview, Settings → System → Repairs → ⋮ → System information has a Mysa Local section: how many thermostats are on the local path, on the cloud or offline, whether you're signed in, whether the password is saved and whether Mysa's server can be reached.

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
