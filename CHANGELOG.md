# Changelog

## Unreleased

- The Baseboard V3, Central HVAC and Climate Sensor are unsupported: to join, they need a credential file from Mysa's servers. HomeKit discovery no longer claims them (the manifest now lists `BB-V1-*`, `BB-V2-*`, `INF-V?-*` and `AC-V?-*`), adding from the Mysa account skips them and the options' account table marks them **Not supported**. The README's model table shows which models are supported.
- **Save my password** (optional, off by default) on the Mysa sign-in. Mysa's sign-in token seems to last about a month and isn't renewed. With the password saved, Mysa Local signs in again by itself when the token stops working; without it, Home Assistant asks you to sign in again as before. The password is stored unencrypted in Home Assistant's configuration. The options page shows whether it's saved; to change that, sign out and sign in again. Re-authentication keeps the choice and replaces a saved password with the new one. Signing out deletes it, and diagnostics redact it.
- Cloud client: `MysaAuth(email=..., password=...)` signs in again when the refresh token is refused.
- A **Humidity** sensor, and the climate entity's current humidity, from the cloud. They're added whenever the cloud reports humidity: the Baseboard Lite does, although its capability data says it has no humidity sensor. Its readings matched a separate sensor in the same room (dew point within 1 °C).
- A **Heater power** sensor (W): the heater wattage while the relay is on, 0 W while it's off. Added with the energy sensor when a wattage is set.
- A Mysa Local section in Settings → System → Repairs → ⋮ → System information: thermostats per connection path, sign-in, saved password, cloud status, poll interval and whether Mysa's server can be reached (only checked with the cloud on).

## 0.2.1 (2026-10-11)

- Repairs warnings: a fixable warning's text now lives only in its fix flow, and the ones Home Assistant can't fix (models it can't add to an account) get their own text-only version. Home Assistant's translation check (hassfest) rejected 0.2.0 for having both.

## 0.2.0 (2026-10-11)

- Local and cloud kept in step (while signed in to Mysa):
  - Pairing a thermostat that isn't on your Mysa account offers to add it there; adding one from the account offers to pair it locally too (found on the network by its `Mysa-xxxxxx` name).
  - Each thermostat's **Settings and status** shows whether it's on the account and paired locally, and can add it to or remove it from the account, or pair it locally.
  - The integration options show a table of the thermostats on the account, with their Home Assistant and Mysa app names (view only; removing happens per thermostat).
  - The device's model line shows the thermostat's Mysa ID and, when it differs, its name in the Mysa app; its settings can sync the name either way.
  - Repairs warnings with fixes: paired but not on the account; cloud only; added from the account but no longer on it. All can be ignored.
  - New diagnostic sensors: **Mysa account** and **HomeKit pairing**.
  - Adding a thermostat to the account keeps its stored settings: the cloud client's `add_device(app_defaults=False)` sends the older app's smaller claim. (The current app's claim reset a returning thermostat's heater type to Baseboard.)
  - The options menu says which Mysa account is signed in.
- Cloud client: `add_device` (claim a thermostat for the account; returns False on 409, already on the account), `delete_device`, `replace_device` (`purgeadd`; not used by the integration), `get_shortcuts`/`update_shortcut` and home names in `Bootstrap.homes`. The flow is in `docs/cloud-api.md`, "Adding and removing a thermostat".
  - The claim body carries the defaults Mysa's app sends per model (Baseboard V1/V2/Lite, In-floor, AC). Models that need a credential file over Bluetooth to join (Baseboard V3, Central HVAC, Climate Sensor) and unknown models raise `ValueError`; the client doesn't add them.
  - New constants: `ModelFamily`, `MODEL_FAMILIES` (every model string the app accepts), `model_family()`, `WIFI_ONLY_FAMILIES`, `DutyCycleOpt`.

## 0.1.4 (2026-10-11)

- Pairing: an unpaired Mysa's HomeKit service dozes off and comes back with a press of a button on the thermostat. The pair screen and the README now say so, and a connection failure keeps the form open with a "wake it and retry" hint instead of ending the flow.

## 0.1.3 (2026-10-11)

- The HomeKit code field accepts any spacing or punctuation ("1234 5678", "123-45-678", ...): only the 8 digits count. A code with the wrong number of digits gets a message saying it has 8 and is printed on the back of the faceplate, and trivial codes like 11111111 get their own message.

## 0.1.2 (2026-10-11)

- The setup and options menus mark the Mysa sign-in as [optional], and the sign-in form lists what it adds.

## 0.1.1 (2026-10-11)

- Diagnostics also redact the thermostat's Mysa device ID (its Wi-Fi MAC) and HomeKit ID.

## 0.1.0 (2026-10-11)

First release. Tested on a Mysa Baseboard Lite (BB-V2-0-L), firmware 3.17.5.13.

- Local control over HomeKit: setpoint, mode, room temperature, real heating state, display units and Identify. HomeKit discovery of Mysa models is claimed by this integration, and pairing takes one step.
- Move a thermostat over from the HomeKit Device integration without its setup code. Entity IDs, names and areas are kept, and the old entry is removed.
- Optional Mysa sign-in (Cognito SRP; only a refresh token is stored) adds button lock, wake and sleep brightness, hardware setpoint limits, temperature offset, heater type, firmware check, cloud connectivity, Wi-Fi signal and the thermostat's own heating runtime.
- Local-first routing: changes go over HomeKit and fall back to the cloud, with a read-back after a timed-out write so nothing is applied twice. A connection-path sensor shows the active path, and a repair issue appears after 15 minutes without the local path.
- While a thermostat runs on the cloud, Mysa's live mode is requested and the poll speeds up to 10 s. Both stop as soon as the local connection is back.
- One cloud request polls all thermostats. Polling backs off on errors and rate limits.
- A cloud off switch makes zero calls to Mysa's servers.
- Heating runtime today (from the thermostat, or counted locally) and heater energy today from a wattage you set, for the Energy dashboard.
- Actions: `hold`, `resume_schedule`, `set_schedule`, `clear_schedule`, plus a schedule-hold sensor.
- Firmware update entity: checking is always on, and installing is opt-in under Advanced options.
- Diagnostics with redaction, translations and brand images.
