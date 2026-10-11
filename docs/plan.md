# Mysa for Home Assistant: integration plan

Written 2026-10-10. Background research: the feature map in `README.md`, request formats in `cloud-api.md`.

## Goals
- One integration for every Mysa model. It's built from Mysa's own capability data, so most models should work without special-casing. **Only the Mysa Baseboard Lite (BB-V2-0-L, firmware 3.17.5.13) has been tested so far**, and the README and setup screens say so.
- Every feature that exists locally (HomeKit) uses the local path. The cloud is used only for features with no local equivalent, and as a fallback when the local connection fails.
- The cloud can be switched off completely. With it off, the integration makes zero calls to Mysa's servers.
- Installed from HACS and set up in the UI, with no YAML.
- Setup links to the browser-based Wi-Fi setup page (https://sokolskynikita.github.io/mysa-wifi-setup/, repo https://github.com/SokolskyNikita/mysa-wifi-setup), so a new thermostat can be put on Wi-Fi from the computer without the Mysa app or a phone.

## Non-goals (for now)
- MQTT. Mysa's broker refused a connection made with regular app credentials (2026-10-10), and HomeKit already pushes changes locally.
- Features HA already does better: Home/Away/Vacation shortcuts, geofencing, Smart Alerts, sharing, voice assistants. See the "Likely redundant" table in `README.md`.
- Adding Baseboard V3, Central HVAC and Climate Sensor thermostats (decided 2026-10-11). To join, they need a credential file from Mysa's cloud sent over Bluetooth, plus fleet registration and a finalize step. A web page can't call Mysa's API (no CORS), so the Wi-Fi setup page can't do it alone either; it now says so. The cloud client refuses these models. The flow and the options for later are in `cloud-api.md`, "Newer models".

## Stack
- Python, async (HA requires both).
- `aiohomekit` for the local side. It's the library behind HA's built-in HomeKit support, so every HA already has it.
- `aiohttp` only for the cloud. Mysa's AWS Cognito SRP sign-in is implemented in `api/srp.py` (no boto3/pycognito at runtime); the tests check it against pycognito and a simulated Cognito server.
- From HA:
  - config flow, options flow and reauth flow
  - `DataUpdateCoordinator` for cloud polling
  - entity platforms
  - diagnostics with redaction
  - repairs
  - `strings.json` translations
- One GitHub repo with the integration in `custom_components/<domain>/`. The cloud client is a self-contained package inside it (`api/`), with no HA imports, so it can be published to PyPI later if we aim for HA core.
- Tools:
  - the `integration_blueprint` template, which includes a dev container
  - `pytest-homeassistant-custom-component`, and aiohomekit's fake controller for HomeKit tests
  - `ruff` and `mypy`
  - GitHub Actions running `hassfest`, the HACS validation action and the tests

## Name and domain
Mysa_HA already uses the domain `mysa`. Using the same domain would make the two clash, so ours needs its own. Proposal: repo `mysa-homeassistant`, domain `mysa_local` (it signals local-first). Decide before the first release; the domain can't change later without breaking installs.

## Connection model

### Which path each feature uses

| Feature | Local (HomeKit) | Cloud | Routing |
|---|---|---|---|
| Room temperature, setpoint, mode, real heating state, °C/°F | ✅ pushed | ✅ polled | Local; cloud only while local is down |
| Identify | ✅ | — | Local only |
| Button lock, brightness, setpoint limits, temperature offset, heater type | — | ✅ | Cloud (when enabled) |
| Schedules, holds | — | ✅ | Cloud (when enabled) |
| Heating runtime | derived from local heating state | ✅ measured by the thermostat | Cloud value when enabled (more accurate, covers HA downtime), else derived locally |
| Change log (who changed what) | — | ✅ | Cloud (when enabled) |
| Firmware check / install | — | ✅ | Cloud (when enabled) |
| Online status, Wi-Fi signal, firmware version | partly (connection state, firmware string) | ✅ | Local where it exists, cloud adds the rest |

For models whose local protocol is Matter (Baseboard V3 and newer; the capability data says `matter: true`), local control comes from HA's own Matter integration. Ours then adds only the cloud extras to the same device (later milestone).

### Cloud setting (options flow)
- **Use Mysa cloud: On** (default when the user signed in). Cloud powers the cloud-only features and is the fallback.
- **Off.** No calls to Mysa's servers at all, and cloud-only entities are removed. The stored sign-in is kept, so turning it back on doesn't require signing in again. "Sign out" deletes it.
- Users who skip the Mysa sign-in at setup are local-only automatically.
- A middle option, "cloud only as fallback, no cloud-only features", stays out of v1 unless someone asks for it.

### Reads
- HomeKit pushes changes for temperature, setpoint, mode, heating state and units, so there's no polling locally.
- Cloud: one `POST /state/batch` for all of the account's thermostats every 60 s (configurable 30–300 s), plus a re-read right after a cloud write.
- While local is healthy, cloud values for the locally available fields are ignored, so two sources can't fight. While local is down, the cloud feeds those fields too.

### Live mode during fallback (added 2026-10-10)
Measured on a Baseboard Lite, 10 setpoint changes per path, times from send:

| | Local (HomeKit) | Cloud (`POST /state/{dev}/update`) |
|---|---|---|
| Write acknowledged | 0.10 s (median) | 0.45 s (HTTP) |
| Thermostat applied it | 0.16 s | 0.57 s |
| Cloud `reported` shows it | ~2.1 s | 1.2 s |

- Room temperature: HomeKit pushes it the moment it changes. The cloud's `latestTelemetry.reading` updates about every 5 s only while "live mode" is on (after the app opens or after commands), then slows to ~40 s and then stops: one test went 2.5+ minutes without a new reading, and the cloud still said 21.5 °C while HomeKit already showed 21.4 °C. So a polled cloud temperature can be minutes old.
- Rule: while a paired thermostat's local connection is down and the cloud is on, request live mode for its home (`POST /mysa-legacy/stream/home/{home}` `{"timeout":300}`), renew it every 4 minutes (the app renews about every 4.2 min) and poll `/state/batch` every 10 s. Stop both as soon as local is back: the poll returns to the normal interval and live mode lapses on its own after 300 s.
- Never request live mode while local is healthy, for thermostats without a local path (cloud-only) or with the cloud off.

### Writes and fallback
1. Send the change over HomeKit with a short timeout (about 5 s).
2. If HomeKit isn't connected or the write fails, and the cloud is enabled, send the same change through `POST /state/{dev}/update`. Then confirm it from the cloud state.
3. After a timeout where the outcome is unknown, read the current state before retrying anywhere, so a change never gets applied twice.
4. Keep retrying the local connection in the background with backoff, and switch back to local as soon as it's healthy.
5. A diagnostic sensor, **Connection path** (`local` / `cloud` / `offline`), shows which path is active. Local down for more than about 15 minutes raises a repair issue with hints (Wi-Fi, DHCP reservation, power).

### Being a good API citizen
- Poll gently, as above.
- Back off on 429 and 5xx responses.
- Never run two refreshes at once, and spread out bursts of writes.
- Send an honest User-Agent that names the integration and its version.
- Refresh the token only when it's about to expire. Mysa doesn't rotate refresh tokens, and they seem to expire after about a month (mysa2mqtt's changelog: its process "died after about a month" until it signed in again with the password). On failure, sign in again with the saved password if the user chose to save it; otherwise go to re-authentication instead of retrying in a loop.

## Setup flow
1. **Discovery.** The manifest lists Mysa's HomeKit model names under `"homekit": {"models": [...]}`, so HA offers a discovered Mysa to this integration, not to its generic HomeKit Device integration.
   Known model strings from the app: `BB-V1-0`, `BB-V1-1`, `BB-V2-0`, `BB-V2-0-L`, `BB-V3-0`, `INF-V1-0`, `AC-V1-0`, `ST-V1-0`, `CS-V1-0`. The list needs checking per model; only `BB-V2-0-L` is confirmed to advertise HomeKit.
   Manual add is also possible (IP or pick from a list).
2. **Not on Wi-Fi yet?** The first step explains it and links to the Wi-Fi setup page. It works in Chrome or Edge on a computer with Bluetooth and doesn't need the Mysa app or a phone. After that, discovery picks the thermostat up.
3. **Pair.** The user enters the 8-digit HomeKit code from the faceplate (only the digits count; spaces and dashes are ignored).
   Observed 2026-10-10 on the Lite (fw 3.17.5.13): **an unpaired Mysa's HomeKit service dozes off.** About 30 minutes after it was unpaired it had stopped advertising `_hap._tcp` and closed its HAP port (Wi-Fi and the cloud connection stayed up). A pair attempt then failed with "couldn't reach". A press of a button on the thermostat brought HomeKit back (same port, `sf=1`, `c#` bumped). The pair screen, the "couldn't reach" error and the README tell users to press a button first, and a connection failure keeps the form open for a retry instead of ending the flow. The thermostat can only be paired with one HomeKit controller at a time, so if it's in HA's HomeKit Device integration or Apple Home, the flow says to remove it there first.
   Nice-to-have: offer to move an existing HomeKit Device entry over without re-entering the code.
4. **Optional Mysa sign-in,** for the cloud-only extras. Thermostats are matched across the two paths by Wi-Fi MAC: Mysa's device ID is the MAC, and HomeKit exposes the MAC in a hidden accessory-info field. The integration stores the refresh token, plus the password only if the user ticks **Save my password** (off by default; changing it later means signing out and in again).
   Cloud-only devices (no local path) can also be added from the account.
5. **Untested model notice.** For any model other than BB-V2-0-L, the confirm step says the model hasn't been tested yet and asks the user to share diagnostics.
6. **Options:** cloud on/off, poll interval, heater wattage (for kWh) and whether to use the cloud's runtime numbers.

## Entities
Created from Mysa's capability data (`GET /capabilities/{dev}`), so each model gets only what it supports. Without the cloud, local-only entities come from HomeKit.

| Platform | Entities |
|---|---|
| `climate` | Setpoint, mode, current temperature, heating state (real relay state), min/max from the hardware limits |
| `sensor` | Heating runtime (today, and a total for statistics); energy (kWh, when a wattage is set); Wi-Fi signal (diagnostic); connection path (diagnostic) |
| `switch` | Button lock |
| `number` | Wake brightness, sleep brightness, min/max setpoint limit, temperature offset |
| `select` | Heater type (Baseboard / Fan Forced), display units |
| `update` | Firmware |
| `button` | Identify |
| `binary_sensor` | Cloud reports thermostat online (diagnostic) |

The energy sensor is shaped for HA's Energy dashboard (device consumption).

## Actions (services)
- `hold` (permanent, or until a time), `resume_schedule`
- `set_schedule` / `clear_schedule`, which write Mysa's on-thermostat weekly schedule. That schedule keeps running if HA or the internet is down.
- Later: an event for "changed at the thermostat" from the change log, so automations can react to someone using the wall buttons.

## Firmware updates
- The check works: `GET /devices/update_available/{dev}`.
- Install is one `POST /devices/{dev}/firmware-update`, and the thermostat downloads the update itself. It's untested and can't be undone, so it ships disabled behind an advanced option until someone with a pending update tests it.

## Disclosure of tested models
- README: "Tested on Mysa Baseboard Lite (BB-V2-0-L), firmware 3.17.5.13. Other Mysa models are supported through Mysa's capability data but untested. Diagnostics and reports welcome."
- The setup notice from step 5 above.
- A table in the README that grows as models are confirmed.

## Testing
- Unit tests for the cloud client with redacted fixtures made from the 2026-10-10 captures.
- HomeKit tests with aiohomekit's fake controller.
- Routing tests: local OK, local down (cloud takes over), cloud off and an unknown write outcome (no double apply).
- Manual matrix on a Baseboard Lite:
  - local only (cloud off)
  - cloud fallback, by temporarily blocking HA → thermostat on the router
  - re-authentication
  - the integration restarting while the thermostat is offline
- Before each release, a test with two thermostats.

## Milestones
1. **Scaffold.** Repo, CI (hassfest, HACS validation, tests, lint), domain decided.
2. **Local only.** HomeKit discovery claimed via the manifest, pairing flow, climate/identify/units, Wi-Fi setup link. Done when it matches HA's HomeKit Device integration for the Lite.
3. **Cloud sign-in.** Auth, re-auth, `/state/batch` coordinator, capability-driven cloud entities (lock, brightness, limits, offset, heater type, signal, online).
4. **Local-first routing.** Fallback, the cloud on/off option, connection-path sensor, repair issue.
5. **Runtime and energy.** Local-derived and cloud runtime, kWh with wattage.
6. **Schedules and holds.** Actions plus hold status.
7. **Firmware.** Update entity: check on, install behind the advanced option.
8. **Release.** README with the tested-models disclosure, a HACS custom-repository release, then a submission to the HACS default list.
9. **Later:**
   - cloud extras on top of HA's Matter integration for V3 devices
   - "changed at thermostat" events
   - Bluetooth Wi-Fi onboarding inside HA for users whose HA has Bluetooth
   - a PyPI library split and an HA core submission

## Decisions (2026-10-10)
- Repo `mysa-homeassistant`, domain `mysa_local`.
- **Entry model:** one Mysa Local config entry (optionally signed in to Mysa) with each thermostat as a **config subentry**. Since HA 2026.x a device belongs to exactly one config entry, so separate "account" and "thermostat" entries would have split each thermostat's local and cloud entities across two devices. One entry also means one `/state/batch` poll and one re-authentication for the whole account. Discovery of a new thermostat adds a subentry to the existing entry.
- No "cloud only as fallback" middle option in v1.
- A thermostat in HA's HomeKit Device integration can be moved over: the flow uses the existing pairing to add a second admin pairing with our own keys (no setup code), then moves the old entities (keeping entity IDs, names and area) and removes the HomeKit Device entry, which removes its own pairing from the thermostat.
- Firmware install stays off by default, behind an advanced option.
- (2026-10-11) Humidity is exposed whenever the cloud reports it, the Lite included; its capability data says otherwise, but the readings are real (see `cloud-api.md`).
- (2026-10-11) Saving the Mysa password is an opt-in at sign-in, so an expired sign-in can renew itself. Most HA integrations store passwords the same way, unencrypted.
