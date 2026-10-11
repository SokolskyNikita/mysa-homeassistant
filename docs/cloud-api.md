# Mysa cloud API, as used by the Android app 4.20.1

Captured 2026-10-10 from a live session of the Android app (traffic through a local TLS proxy), on an account with one Mysa Baseboard Lite (BB-V2-0-L, firmware 3.17.5.13).
Everything below was observed live, except in sections that say they come from the app's code and items marked *not captured*. Account data (email, names, user/home IDs, tokens) is left out on purpose; `{user}`, `{home}`, `{dev}` are placeholders. `{dev}` is the lowercase Wi-Fi MAC without colons.

## Transport
- **HTTPS only.** The app opened no MQTT/AWS IoT connection at all. The thermostat itself still holds its own MQTT session to AWS IoT (:8883); Mysa's backend relays commands over it.
- Hosts: `mysa-backend.mysa.cloud` (almost everything), `app-prod.mysa.cloud` (only `GET /users/pushNotification`; also returned as `regionApiBaseUrl` by bootstrap), `i18n.mysa.cloud` (static country/timezone lists), `chunk.mysa.cloud/track` (analytics).
- Other traffic: Firebase/Crashlytics, customer.io (in-app messaging), SNS `CreatePlatformEndpoint` (push registration).
- **Not usable from a web page (CORS, checked 2026-10-11).** Preflights were sent the way a browser sends them (`Authorization` and `Content-Type` only), from five origins including Mysa's own `https://app.mysa.cloud`, plus real authorized requests that carried an `Origin` header.
  - `mysa-backend`: every preflight → 404 with no CORS headers, and real responses have no `Access-Control-Allow-Origin` either.
  - `app-prod`: preflights for its own routes (e.g. `/devices`) pass (`Access-Control-Allow-Origin: *`, `Authorization` allowed, methods `OPTIONS, GET, POST`). But the real responses lack `Access-Control-Allow-Origin`, so a page can send requests there and can't read the answers. The `mysa-backend`-only routes (`/pairing/*`, `/state/*`, bootstrap) don't exist on `app-prod` (403).
  - Cognito (`cognito-idp`) allows any origin, so signing in from a page would work; the API calls after it wouldn't.

## Auth (AWS Cognito, us-east-1)
- User pool `us-east-1_GUFWfhI7g`, app client `6cktj934gasnc72f7jo2cmf6rt` (no client secret), identity pool `us-east-1:e27f246e-ad23-41ce-ac80-eda86f8be424`. These are app-level IDs baked into the app, not user secrets.
- Sign-in: `InitiateAuth` `USER_SRP_AUTH` → `PASSWORD_VERIFIER` challenge → `RespondToAuthChallenge` (`PASSWORD_CLAIM_SIGNATURE`, `PASSWORD_CLAIM_SECRET_BLOCK`, `TIMESTAMP`, `USERNAME`). Result: `IdToken`, `AccessToken`, `RefreshToken`, `ExpiresIn` 3600. No device tracking (`NewDeviceMetadata` absent).
- Renewal: the app calls `GetTokensFromRefreshToken` (`{"ClientId", "RefreshToken"}`) once the ID token has expired. The answer has a new `IdToken`/`AccessToken` (`ExpiresIn` 3600) and no new refresh token, so refresh tokens aren't rotated.
- Requests go to `https://cognito-idp.us-east-1.amazonaws.com/` with `X-Amz-Target: AWSCognitoIdentityProviderService.<Action>` and `Content-Type: application/x-amz-json-1.1`; no AWS signature is needed for this public client.
- API calls send the **raw Cognito ID token** in `authorization:` (no `Bearer` prefix).
- The identity pool (`GetId` + `GetCredentialsForIdentity`) gives temporary AWS keys. The app only used them to sign the SNS push registration. An integration that doesn't use MQTT doesn't need them.

## Reading state
- `POST /state/batch` `{"deviceIds":["{dev}"]}` → `{"{dev}": {"data": <state>}}`. The app polls this **every ~2.9 s** while open.
- `GET /state/{dev}` → `<state>` (the app reads it twice right after each write).
- `<state>` (shadow style: `desired` = last command, `reported` = what the thermostat confirmed; timestamps are Unix seconds):
  - `latestTelemetry.isConnected`, `lastConnected` and `reading`: `roomTemperature`, **`humidity`**, `rawTemperature`, `coreTemperature`, `heatSetpoint`, `effectiveSetpoint`, `mode` (3 while heating mode was on), `dutyCycle`, `onTime`, `rssi`, `freeHeap`, `voltage` (240, configured, not measured), `maxCurrent` 0, `powerConsumed` 0, `instantLoad`.
  - `targetHeat.{desired,reported}.setpoint`; `reported.lockoutMin`/`lockoutMax` (hardware setpoint limits, 5/30).
  - `modes.{desired,reported}.mode` (4 = heat here; note `reading.mode` was 3 at the same time).
  - `physicalInterface.{desired,reported}`: `lockout` (button lock), `activeIntensity` (wake brightness %), `idleIntensity` (sleep brightness %), `format` ("C"), `intensityMode`.
  - `identity.reported`: `family` "BB", `model`, `fw`.
  - `tracking.reported.ambientOffset` (temperature offset).
  - `power.reported`: `voltage`, `current`, `wattage`, `dutyCycle` (all 0 on the Lite: it has no current sensor).
  - `bbConfig.reported.controlType` (heater type: 8 = Fan Forced, 9 = Baseboard; this unit is set to 8).
  - `diagnostics.reported.freeHeap`, `drState` {inEvent, state}.
- Live mode: `POST /mysa-legacy/stream/home/{home}` `{"timeout":300}` → `{"message", "streamedDeviceIds", "skippedDeviceIds", "failedDeviceIds"}`. Sent on open and then every ~4.2 min. It makes the thermostats report every ~5 s; the app still polls `/state/batch` for the data. Without it the reading slows to ~40 s and then can stop for minutes (measured 2026-10-10: 2.5+ min without a new reading).

## Writing state: `POST /state/{dev}/update`
Body is `{"source":3, <section>: {<fields>}}`; the reply is `{"message":"Successfully updated state for device {dev}"}`. Each change below showed up in `reported` within about a second.

| Change | Body |
|---|---|
| Setpoint | `{"source":3,"targetHeat":{"setpoint":22.5}}` |
| Button lock on / off | `{"source":3,"physicalInterface":{"lockout":3}}` / `{"lockout":0}` |
| Brightness (wake / sleep %) | `{"source":3,"physicalInterface":{"activeIntensity":90,"idleIntensity":50}}` (the app always sends both) |
| Mode off / heat | `{"source":3,"modes":{"mode":0}}` / `{"mode":4}` |
| Hardware setpoint limits | `{"source":3,"targetHeat":{"lockoutMax":29.5}}`, `{"targetHeat":{"lockoutMin":5.5}}` (0.5 steps) |
| Temperature offset | `{"source":3,"tracking":{"ambientOffset":-0.5}}` (−5…+5, 0.5 steps) |

All of the above were captured 2026-10-10 and set back afterwards.

### Liveness and latency probes (measured 2026-10-11, from the SSH add-on)
- `GET /state` only reads Mysa's stored copy (~100 ms with keep-alive). It says nothing about the thermostat right now: `isConnected` was true while the latest reading was 305 s old.
- **Same-value writes are not a ping.** `ambientOffset` 0 → 0 and an unchanged brightness pair both returned 200, but `reported.timestamp` never moved (10 s, live mode on). Only real changes are acknowledged.
- **The live-mode request is the usable cloud ping:** `POST /mysa-legacy/stream/home/{home}` `{"timeout":20}`, then poll `/state` until `latestTelemetry.reading.timestamp` moves. The first fresh reading came 0.86 / 0.95 / 1.1 / 1.17 / 1.35 s after the request (5 rounds, ±0.25 s polling granularity). The short timeout is honored: readings stopped ~25 s later. `streamedDeviceIds` alone proves nothing; wait for the reading.
- The true no-op is the legacy MQTT echo (MsgType 6, EventType 0xFFFFFFFF, EchoID 418 → MsgType 5), but this account can't open an MQTT session (see the 3.54.1 notes on `AttachPrincipalPolicy`).
- Local, for comparison: a HomeKit read of current temperature (aid 1, iid 261) over an open session took median 59 ms back to back (31–280) and 139 ms when spaced 2 s apart (57–191). The gap is the thermostat's Wi-Fi power saving. ICMP ping: 4–148 ms. A fresh connection (pair-verify + accessory database) took ~1 s.

### Remote temperature tracking: not supported on the Lite (tested 2026-10-11)
- Newer models can control on a remote reading ("Linked Sensors": Climate Sensors linked to a thermostat via `GET/POST /linking` `{sourceId, targetId, weight}`; the menu only shows for central-HVAC thermostats in 4.20.1). The app switches the thermostat with `{"source":3,"tracking":{"tracking":2,"remoteTTL":480}}` (tracking mode 0 = ambient, 1 = probe, 2 = remote; `DEFAULT_REMOTE_TTL_SECONDS` 480). The cloud feeds the readings itself; the app has no call that writes a temperature.
- The Lite's `/capabilities` says no: `sensing.temperature.sensors.remote: false`, `trackingSensor.validValues: ["internal"]`, `hardware.sensors.externalTemperatureProbeConnector: false`. `GET /linking?targetId={dev}` → `[]`.
- Tried anyway, in slow mode and in live mode: the server accepts the write (it lands in `tracking.desired`), but the thermostat never reports `tracking` back. A control write in the same session (`ambientOffset` 0.5) was acknowledged within ~1 s and moved `roomTemperature` (and HomeKit's current temperature in HA) by +0.5.
- Set back with `{"tracking":{"tracking":0,"ambientOffset":0}}`. `desired` keeps `tracking: 0` and `remoteTTL: 480` afterwards; both are inert.
- The closest working substitute is `ambientOffset` (−5…+5, 0.5 steps): it shifts both the control temperature and the reported room temperature.

## Writing settings: `POST /devices/{dev}/` (note the trailing slash)
Some settings still go through the older device record. The reply echoes the body.

| Change | Body | Notes |
|---|---|---|
| °C / °F | `{"Format":"fahrenheit"}` / `"celsius"` | Reached the thermostat: HA's HomeKit units entity flipped within seconds. `/state` `physicalInterface.format` stayed "C". |
| Heater type | `{"DutyCycleOpt":9}` (Baseboard) / `8` (Fan Forced) | `/state` `bbConfig.controlType` didn't change within 30 s. |
| Rename | `{"Name":"Bedroom"}` | |
| Time zone | `{"TimeZone":"America/Los_Angeles"}` (IANA name) | |

## Schedules and holds (captured 2026-10-10 with a temporary schedule, deleted afterwards)
- Create: `POST /schedule` → 201 `{"scheduleId":…}`. Body: `{"name":"Schedule-{dev}","deviceType":"BB","homeId":"{home}","reusable":false,"deviceIds":["{dev}"],"events":[{"executionTime":"20:45:00","dayOfWeek":"6","clientKey":"App-4.20.1","state":{"targetHeat":{"setpoint":22},"modes":{"mode":4}}}]}`. `dayOfWeek` 0 = Sunday … 6 = Saturday. Local time in the device's time zone.
- Read: `GET /schedule?homeId={home}&includeLegacyBaseboard=true&includeLegacyAc=true&includeLegacyInfloor=true` → list of `{id, name, reusable, homeId, deviceType, deviceIds, events[], createdAt, updatedAt}`. 404 when there's none.
- Delete: `DELETE /schedule/{scheduleId}/attach/{dev}` → "Schedule unattached from device and deleted".
- Editing an existing schedule is *not captured* (the route list has `UPDATE_SCHEDULE /schedule/`).
- **Holds:** with a schedule active, a plain setpoint change (`/state/{dev}/update`) automatically holds until the next event, and the app shows "Holding until …".
  - Permanent hold: `POST /schedule/hold` `{"deviceId":"{dev}","holdType":"permanent","startTime":"<ISO UTC>","state":{"modes":{"mode":4},"targetHeat":{"setpoint":22.5}}}` → 201 `{"holdId":…}`.
  - Timed hold: the same body with `"holdType":"temporary"` and `"endTime":"<ISO UTC>"`. The holdId stays the same, so this replaces the existing hold.
  - Resume schedule: `DELETE /schedule/hold/{dev}` → "Device restored to follow its schedule". The setpoint snapped back to the schedule's value.
  - Hold status: `GET /schedule/hold/{dev}` (404 when there's no hold).

## Shortcuts (Home / Away / Vacation)
- Edit: `PATCH /scene/{home}_away` → 202. Body: `{"DeviceActions":{"{dev}":{"type":3,"mode":4,"setpoint":22,"holdTillKey":0,"secToHold":null,"version":"V2"}},"AllDevices":false}`. The app's UI can't remove a device's command again. To reset it, PATCH `{"DeviceActions":{},"AllDevices":true}` directly (done 2026-10-10).
- Apply: `POST /homes/shortcut/apply/{home}_away` `{"Timestamp":<unix s>}` → `Ok`.

## Read-only extras that work for the Lite (direct calls, 2026-10-10)
- **Firmware check:** `GET /devices/update_available/{dev}` (on either host) → `{"update":false,"installedVersion":"3.17.5.13","allowedVersion":"3.17.5.13"}`. `GET app-prod/devices/firmware` → installed version per device. The newer `GET /ota/update/available/{dev}` → 404 "No fleet found containing device" (the Lite isn't in the new OTA system).
- **`GET /capabilities/{dev}`** (the app calls it with `?includeLegacyBaseboard=true&includeLegacyAc=true&includeLegacyInfloor=true`; it works without too) returns a full capability document for the Lite: mode off/heat; setpoint 5–30 in 0.5 steps; controlType 8/9; offset −5…+5; lockout 0/3; unit C/F; `humiditySensor: false`, `proximitySensor: false`, `ambientLightSensor: false`; connectivity homekit/googleHome/alexa true, matter false; smart: scheduling, geofence and shortcuts true; energyReporting, earlyOn, zoning and savedSchedules false.
  Despite `humiditySensor: false`, the Lite's `humidity` (35–37 %) looks real (checked 2026-10-11). Its readings gave a dew point of 6.4–6.5 °C at 01:29 and 01:48, against 5.6 °C from an Apollo AIR-1 (SEN55) in the same room. The value went from 36 to 37 % as the room cooled from 22.2 to 21.8 °C, as a constant dew point predicts. It's compensated for the board's self-heating: uncompensated, the 27 °C `rawTemperature` would read about 25 %. Mysa Local exposes it. A longer comparison could use the usage endpoint's ~30 s `rawReadings` `h` values.
- **`GET /devices`** (legacy settings record): `ButtonState`, `Format`, `DutyCycleOpt`, `HeaterType` "radiant", `MinBrightness`/`MaxBrightness`, `MinSetpoint`/`MaxSetpoint`, `TimeZone`, `UserTempOffset`, `ecoMode` 1, `Animation` "off", `ButtonThreshold` and other button-timing values, `LastPaired`, `Voltage`, `MaxCurrent`.
- **`GET app-prod/devices/state`** (legacy state, value+timestamp per key): `CorrectedTemp`, `SensorTemp`, `HeatSink`, `Humidity`, `SetPoint`, `Duty` (0/1 = relay), `OnTime`/`OffTime`, `Lock`, `Brightness`, `Mode`, `TstatMode`, `ScheduleMode`, `HoldTime`, `Rssi`, `Connected`, `Current`, `Voltage`, `FreeHeap`.
- `GET /devices/{dev}/telemetry_alerts` → `{"Items":[]}`. `GET /smart-early-on/{dev}/availability` → `available:false`, `model-not-eligible:BB-V2-0-L` (server-enforced). `GET /homes` → home with `Zones: []`.

## History endpoints the app hides for the Lite (both work; direct calls 2026-10-10)
- **Usage / heat runtime:** `POST /telemetry/usage/{dev}` with `{"timezone":"America/Los_Angeles","start":<unix s>,"end":<unix s>,"summary":{"oH1":"sum","k":"sum","t":"average","h":"average","n":"sum"},"aggregatedReadings":["oH1","k","t","h","n"],"rawReadings":["t","c","h","i","s","hs"]}`. The app sends `rawReadings` only for a one-day range, and `handleUsageFetch` returns early for the Lite.
  The response is a list per day: `summary.a` = {`oH1` heat on-time in seconds, `t` average temperature, `h` average humidity, `n` sample count}; `aggregatedReadings` = 30-minute buckets of the same; `rawReadings` = ~30 s samples {`t` time, `c` room °C, `h` humidity, `s`/`hs` setpoint}. `k` (energy) is absent: the Lite has no current sensor.
  Use: heater runtime → kWh with the heater's wattage, and back-filling temperature history.
- **State change log:** `GET /state/history/{dev}?startTime=<unix s>&endTime=<unix s>` (integers, seconds) → per field (`heatSetpoint`, `coolSetpoint`, `mode`, …) a list of `{timestamp, value, source}`. `source` is one of `device` (buttons on the thermostat), `app`, `schedule`, `shortcuts`. The app defines this route but never calls it.
  Use: telling whether a change came from the thermostat's buttons, a schedule or a shortcut.
- `POST /energy/v3/{home}/home` → 404 (whole-home energy; not for the Lite, and of little value without a current sensor).

## Firmware updates (static analysis of the app, 2026-10-10; nothing was triggered)
- **The phone never downloads or pushes a firmware image.** The Lite uses the legacy path: the app asks Mysa's cloud to start the update and the thermostat fetches the image itself (presumably the backend tells it over MQTT; the app holds no image URL). The only BLE file transfer is the pairing **credentials** blob from `POST /pairing/credentials`, and only for newer models.
- Legacy flow (BB-V1/V2/Lite, In-floor, AC):
  1. `GET /devices/update_available/{dev}` → `{update, installedVersion, allowedVersion}`.
  2. If `update` isn't false: `POST mysa-backend /devices/{dev}/firmware-update` with **no body**.
  3. Re-check every 20 s (the pairing flow even re-POSTs each time while `update` is true) until `update === false`. The app gives up after 6 min. The home-screen variant polls `update_available` every 10 s and then reads `GET /devices/firmware`.
- In 4.20.1 the app only starts a Lite update right after pairing. The home-screen update sheet never opens for a Lite.
- Newer models use `/ota/update/available/{dev}`, `POST /ota/update` `{deviceIds, versionId, requestSource:"USER", …}`, `GET /ota/update/{requestId}` and `PUT /ota/update/{requestId}/cancel`, plus fleet registration at pairing. The Lite isn't in any fleet (404).
- For an HA `update` entity: the check is verified live; install = the single POST above (untested, irreversible).
- App detail: the `legacyBaseboardMigration` flags are hard-coded true in 4.20.1. That's why the Lite gets `/state`, `/capabilities` and `/schedule` like newer models, while firmware, pairing and fleet stay legacy.

## Adding and removing a thermostat (static analysis of 4.20.1 and 3.54.1, 2026-10-11)
From both app bundles, decompiled with `hermes-dec`. The claim, the delete and `/devices/purgeadd` were also called for real (see the live test below); the rest is read from the code.

### Add (claim): `POST /devices`
- **Where it happens during setup (4.20.1):** Wi-Fi setup over Bluetooth or the thermostat's own access point → `CONNECTED` → **claim** (`ADDING_TO_ACCOUNT`) → connection check (`VERIFYING_CONNECTION`; for the Lite a "legacy echo" check that gives up after 4 min) → firmware update → done.
  So the app claims the thermostat as soon as it has handed over the Wi-Fi details, before the cloud has seen it online. Whether the server itself requires the thermostat to be online is unknown.
- 4.20.1 sends it to `mysa-backend`, 3.54.1 to `app-prod`. Body for a Lite (4.20.1, `usePairingService` → `addDevice`):
  ```json
  {"Id": "{dev}", "Name": "<name chosen in the app, else {dev}>", "Model": "BB-V2-0-L", "Home": "{home}",
   "TimeZone": "<the phone's IANA time zone>", "LastPaired": <unix s>,
   "Animation": "off", "ecoMode": 1, "Format": "celsius", "schedGlobalOffset": 0, "DutyCycleOpt": 9}
  ```
  - `Id` is the thermostat's Wi-Fi MAC (lowercase, no colons). Nothing in the body proves you have the thermostat; it's only the ID.
  - `Format` follows the user's °C/°F preference. `DutyCycleOpt` 9 is Baseboard, so a freshly added Lite starts as Baseboard, not Fan Forced.
  - Other models:
    - AC adds `SetupRequired: true`.
    - In-floor adds floor-sensor defaults (`FloorOhms` 10, `FloorType`, `IsMysaFloorSensor`, `MaxFloorTemp`, `MaxSetpoint`, `Tracking`).
    - BB-V1 gets `DutyCycleOpt` 0.
    - `DUTY_CYCLE_OPT` values: 0 BB1 baseboard, 4 BB1 radiant, 5/6/7 BB1 fan forced short/medium/long, 8 BB2 fan forced, 9 BB2 baseboard.
  - 3.54.1 sent only `Id, TimeZone, Name, LastPaired, Home, Model`, plus `SetupRequired` for AC and `schedGlobalOffset: 0` for the Lite. `Home` was `"PROPERTY_HOME"` for property-manager accounts.
- The app's HTTP client counts **409 as a success** (`{ok: true, statusCode: 409}`), like 202 and 204.
- **Outcomes:**
  - **2xx:** added. The app reloads the account (`fetchEssentialData`) and creates the home's default shortcuts if it has none.
  - **409: the thermostat is already on *this* account** (confirmed live; see the test below).
    - 3.54.1 said so in a dialog: "This thermostat was previously added to your account, would you like to use the same name, schedule and settings?"
    - **Keep** used the existing record as is. **Overwrite** sent the same body to **`POST /devices/purgeadd`** (`app-prod`), which replaces the old record.
    - 4.20.1 has no dialog and no `purgeadd`. On 409 it always keeps the old record, only sets `ecoMode: 1` (`POST /devices/{dev}/` on `mysa-backend`) and logs the pairing as "re-paired".
    - `/devices/purgeadd` still answers: checked by hand on 2026-10-11.
  - **On another account:** if the error text contains "User is not allowed to access the specified home", the app shows "Can Not Pair This Mysa: This Mysa is associated with another user's account…" with a Contact Support button. That's the only ownership check the app knows about, and the server does it.
  - "No Internet Connection" is retried up to 3 times, 3 s × attempt number apart. Any other error shows "Failed To Add Mysa to Account".
- Newer models (Baseboard V3, Central HVAC, Climate Sensor) need more than this claim; see "Newer models" below. The Lite and the other older models need only the claim.

### Remove: `DELETE /devices/{dev}`
- No body. The route is `DELETE_DEVICE`, one of the app's "legacy" routes. Those go to `app-prod`, unless the account's `MONOLITH_API_ACCESS` feature flag is on, which sends them to `mysa-backend` instead.
  The flag was on when checked (see Feature flags below), so 4.20.1 sends this to `mysa-backend`. Both hosts work: the live tests below used each once.
- It's behind Settings › Delete Device ("This will remove the device from your account. Are you sure?"). Order in 4.20.1 (`useDeviceDeletion`):
  1. Only the home's admin may delete. A shared user gets "Only the primary homeowner can remove Mysa devices." The app enforces this; whether the server does too is unknown.
  2. Take the thermostat out of every shortcut: each Home/Away/Vacation shortcut that lists it is saved again without it.
  3. Remove its schedule: an old-style one (a `Schedule` field on the device record) through `/schedules/v2`, or a new-style one with `DELETE /schedule/{id}/attach/{dev}`.
  4. Delete sensor/HVAC links and clear `GroupName` on grouped devices. The Lite has neither.
  5. `DELETE /devices/{dev}`, then drop it from the app's local data.
- Deleting only removes the account's access. The thermostat's state (setpoint, mode, limits, brightness, heater type) survives, and so do the record fields the claim body doesn't name. The fields the body does name replace the stored ones (live tests below).

### Live test: delete, then re-add (2026-10-11)
On a Baseboard Lite that was on the account, online and paired to HA over HomeKit. It had no schedule, no hold, no shortcut actions and no alerts, so the app's cleanup steps had nothing to do. Everything was snapshotted first.
- `DELETE app-prod /devices/{dev}` → **204**, no body.
  - It disappeared from `GET /devices` on both hosts and from bootstrap `AllowedDevices`.
  - `GET /state/{dev}` → 404 "User does not have access to this device". `/state/batch` → `{"{dev}": {"error": {"code": "PERMISSION_DENIED", …}}}`.
  - The thermostat itself didn't notice: it stayed on Wi-Fi and online with the cloud, and HomeKit control kept working.
- 21 s later, `POST mysa-backend /devices` with the 4.20.1 body above (keeping `DutyCycleOpt` 8, its real heater type) → **201** `{}`. Not 409.
  - The record came back identical: name, time zone, brightness range, setpoint limits, button timings, `ecoMode`, `DutyCycleOpt`. Only `LastPaired` was new.
  - **The body's fields overwrite the stored record**: `LastPaired` took the new value. Everything matched only because the body carried the thermostat's real name, heater type and format. Fields the body leaves out (brightness range, setpoint limits, button timings, `Voltage`) came back with the same values; they may be kept or may be Mysa's defaults, which happen to match here. So when re-adding, send the real values, or the app's defaults (name = ID, `DutyCycleOpt` 9) replace them.
  - The thermostat's state came back identical too: setpoint, mode, hardware limits, brightness, lock, offset, `controlType`. So did its capabilities and the home's shortcuts.
  - Telemetry continued, and live mode (`/mysa-legacy/stream/home/{home}`) streamed it again at once.
- Second run, 14 min later, with the delete on **`mysa-backend`** (the host 4.20.1 uses): `DELETE mysa-backend /devices/{dev}` → 204 with the same effects; re-add 12 s later → 201; record, state, capabilities and shortcuts identical except `LastPaired`.
- So a 409 isn't "deleted earlier". **A 409 means the thermostat is still on the account** (tested the same day, 2 min after the re-add):
  - `POST /devices` again with the same body → **409** on both `mysa-backend` and `app-prod`. The response body is the existing device record (`Owner`, `AllowedUsers`, `Name`, all settings, the earlier `LastPaired`).
  - A 409 writes nothing. A body with a different `LastPaired` also got 409, and the stored value stayed the old one. Record and settings were unchanged.
  - That's why the 4.20.1 app can treat 409 as success. The old app's "previously added… Keep/Overwrite" dialog was for re-pairing a thermostat that never left the account; Overwrite (`purgeadd`) was the only way to replace the record.
- The claim needed nothing but the ID. Whether a thermostat that was never on this account can be claimed the same way is still untested.
- **A re-add with the 4.20.1 body resets settings.** A later remove and re-add with the app's default body (`DutyCycleOpt` 9) changed the stored heater type from Fan Forced to Baseboard. The thermostat itself kept reporting `controlType` 8. Sending the 3.54.1 body instead (`Id`, `Name`, `Model`, `Home`, `TimeZone`, `LastPaired`, plus `schedGlobalOffset` 0 on a Lite) leaves stored settings alone.
- Mysa Local in HA: in the first run the cloud entities went unavailable for one poll (60 s) and came back on their own; the second run's 12 s gap fell between polls. Local control wasn't affected.

## MQTT (AWS IoT) test, 2026-10-10 ~21:12 PT
- Tried Mysa_HA's route with a regular app account: temporary AWS keys from the app's own `GetCredentialsForIdentity`, a SigV4-signed WebSocket to `a3q27gia9qg3zy-ats.iot.us-east-1.amazonaws.com/mqtt`, random client ID.
- TLS and the WebSocket upgrade succeed (HTTP 101, subprotocol `mqtt`), so the signature is accepted. **The broker then drops the connection right after the MQTT CONNECT** (4 attempts, no CONNACK). Most likely this identity is no longer allowed to connect; the exact cause wasn't pinned down, and no workarounds were tried.
- Nothing was subscribed or published, and the thermostat was untouched. Treat MQTT as unavailable for app accounts: the integration should use HTTPS (cloud) + HomeKit (local).

## Enumerations (from the app bundle, 4.20.1)
- `modes.mode` (`CONTROL_MODE_TYPE`): 0 off, 1 auto, 3 cool, 4 heat, 5 emergency heat, 6 pre-cool, 7 fan only, 8 dry, 9 sleep. The capability document names them `off`, `auto`, `cool`, `heat`, `emergencyHeat`, `preCool`, `fanOnly`, `dry`, `sleep`.
- Which section holds a mode's setpoint (`controlModeSetpointMapping`): cool → `targetCool`, heat and emergency heat → `targetHeat`, auto → `targetAuto`.
- Fan speeds (`CONTROL_FAN_SPEED`): 0 off, 1–7 speed 1–7, 8 sleep, 9 auto. Swing (`CONTROL_SWING`): 0 invalid, 1 off, 2 on, 3 auto, 4–10 positions 1–7, 11 toggle, 12 n/a.
- State sections (`CONTROL_SHADOW_TYPE`): `latestTelemetry`, `targetHeat`, `targetCool`, `targetAuto`, `modes`, `tracking`, `physicalInterface`, `hvacConfig`, `identity`, `telemetry`.
- `latestTelemetry.reading.mode` uses a different numbering (3 while the Lite heats).
- `latestTelemetry.reading.dutyCycle` is the heater relay share for the last reading (0 = off). The legacy `/devices/state` has the same as `Duty` 0/1.

## Newer models: Baseboard V3, Central HVAC, Climate Sensor (not supported)
Decided 2026-10-11: this integration doesn't add these models for now, and the cloud client refuses them (`ValueError`). Everything below comes from the app's code (4.20.1); none of these thermostats was available to test.

**Why they're different.** Wi-Fi details aren't enough for them to join. The app also sends each one a credential file from Mysa's cloud over Bluetooth, then registers it for updates and finalizes the pairing.

**Setup order in the app**, as far as the code shows:
1. Over Bluetooth: the same Wi-Fi steps as the older models, plus the credential file. The file comes from `POST mysa-backend /pairing/credentials {"deviceId", "model", "serialNumber"}`, whose `credentials` field is base64. The serial number is read from the thermostat over Bluetooth (Device Information `2a25`).
2. The thermostat reports "register device" over Bluetooth. The app then registers it with an update fleet: `POST /ota/fleet/devices {"deviceIds": ["{dev}"], "fleetName": "<type>-prod"}`, with `bb3-prod`, `lv-prod` (Central HVAC) or `cs-prod`.
3. The claim: `POST /devices` with the base fields only (no `DutyCycleOpt`/`schedGlobalOffset`).
4. Connection check: the app waits up to 5 min for the `identity` section of `GET /state/{dev}` to show a timestamp newer than before setup.
5. `POST /pairing/finalize {"deviceId"}`.
6. Firmware through the newer update system: `GET /ota/update/available/{dev}`, `POST /ota/update`, `GET /ota/update/{requestId}`, `PUT /ota/update/{requestId}/cancel`.

**The Bluetooth credential transfer.** Service `ecaa050b-0da4-e394-d64c-5d08e47cb41d`:
- Control characteristic `708aa387-184f-fca4-6b4e-eef896ec9bb5`. Commands: 0 none, 1 start, 2 cancel.
- Data characteristic `81838135-4ca1-96b1-1b47-6e96772fef72`. The app sends the decoded file in chunks sized to the connection.
- Status: 0 idle, 1 downloading.
- Not extracted yet: the exact start payload, the chunk size and how completion is signaled.

**Local control is Matter over Wi-Fi.**
- After setup, `GET /matter/qr/{dev}` returns `{qrCode, pairingCode}`.
- The app tells the user to paste the code into any Matter app's "add device". That's pairing a device that's already on the network; the app never mentions Thread.
- Unlike the HomeKit code, this code isn't printed on the thermostat; it comes from Mysa's cloud. So the first Matter pairing needs the cloud once, and control is local after that.
- In HA this would go through HA's own Matter integration (Matter Server add-on), not this one.

**Why the Wi-Fi setup page can't do it alone.** A web page can't call Mysa's API (CORS, see Transport), so it can't fetch the credential file. If this is picked up later, something outside the browser has to make the Mysa calls:
- A small proxy, for example a Cloudflare Worker that forwards only the pairing calls and adds CORS headers. The Mysa sign-in would pass through it.
- A page served by HA, which makes the Mysa calls with the sign-in it already holds.
- A browser extension, which may ignore CORS for the sites it declares.

## Feature flags and network diagnostics (direct calls, 2026-10-11)
- **`GET mysa-backend /feature-flags/features/check?versionNumber=4.20.1`** → `{"features": {"<NAME>": {"value": true|false}, …}}`. The app loads these at start (`useFeatureFlagAPIService`).
  - All true, except `HIDE_MATTER_IN_APP` false.
  - Host routing:
    - `MONOLITH_API_ACCESS` makes `useAPI` send the app's "legacy" routes to `mysa-backend` instead of `app-prod`.
    - `DISABLE_LEGACY_FALLBACK` turns off retrying them on `app-prod`.
    - So with both on, the current app talks to `mysa-backend` only. That matches the live capture, where `app-prod` saw just one early call.
  - Migrations: `LEGACY_BASEBOARD_MIGRATION`, `LEGACY_AC_MIGRATION`, `LEGACY_INFLOOR_MIGRATION`. They move older models onto `/state`, `/capabilities` and `/schedule`.
  - Other models only:
    - Central HVAC (`LV_*`): `LV_ACCESS`, `LV_AUTOMODE_FW`, `LV_ADAPTIVE_BRIGHTNESS_FW`, `LV_STAGED_AUX_HEAT_FW`, `LV_FREEZE_PROTECTION_ENABLE_FW`, `LV_TEMP_DIFFERENTIAL_FW`, `LV_EXTERNAL_PROBE_FW`.
    - Baseboard V3: `BB3_SMART_EARLY_ON_ACCESS`, `ENROLL_BB3_DR`.
    - General: `SMART_EARLY_ON_V2_ACCESS`, `ADAPTIVE_BRIGHTNESS_ACCESS`, `STAGED_AUX_HEAT_ACCESS`, `DR_ENROLMENT_WEB_FLOW_ACCESS`.
  - Also `MQTT_HOME_BASED_CLIENT` (purpose unknown; the app has no MQTT client).
- **`POST mysa-backend /network-diagnostics/test` doesn't test anything.** The app's Network Diagnostics screen runs its own checks on the phone and then uploads the results there: `{"TestResults": {"WifiConnection": …, "SpeedTest": …, "MysaConnectivity": …}}`. Nothing reaches the thermostat. Not called, since it would only store a made-up result.
- **`GET mysa-backend /network-diagnostics/history`** reads past uploads back: `{"items": []}` on an account that never ran the screen.

## Not captured on purpose
- Geofencing and Smart Alerts: both need the phone's location/notification permissions (declined), and on the Lite the alerts are only geofence enter/leave. HA does presence itself.
- Firmware install (`/devices/{dev}/firmware-update`) and Shared Access invites: irreversible, or they email people.

## Other endpoints seen (all GET unless noted)
- `/mobile/bootstrap?versionNumber=4.20.1`: `versionNumber` is required (without it: 400 `FST_ERR_VALIDATION`, "querystring must have required property 'versionNumber'"). User record (`AllowedDevices`, `AllowedHomes`, Cognito attributes), `primaryHomeId`, `activeHomeId`, `regionApiBaseUrl`.
- `/homes/{home}/scenes`: Home/Away/Vacation shortcuts (`Id` = `{home}_home|_away|_vacation`, `DeviceActions` empty here). Editing and applying are in the Shortcuts section above.
- `/homes/{home}/savedSchedule` → `{"SavedSchedules":[]}`; `/schedule/compatibility/{dev}` → `[]`. Schedules are covered in the section above.
- `/homes/{home}/geofences`, `/homes/reminders/{home}`, `/homes/{home}/users`, `/invite/users/{user}`, `/devices/{dev}/drstate`, `/communication-preferences/`, `/preferences/User_{user}_Home_{home}…`.
- `POST /energy/setpoints/device/{dev}` `{"Scope":"Day","Timestamp":…,"PhoneTimezone":"America/Los_Angeles"}` → setpoint change history (`Prev`, `Next`, `Source`, `SourceTime`).
- `PATCH /users/{user}` `{LastAppVersion, OperatingSystem, DeviceType, LanguagePreference}` on sign-in; `POST /users/pushNotification` (push token).

## App screens for the Lite (what exists to automate)
- Thermostat: setpoint ±, mode (Heat), Add Schedule (wizard or manual), Settings.
- Settings: Thermostat Info (name, time zone, MAC, model, version); Display (sleep/wake brightness, button lock); Preferences (hardware setpoint limits, heater type Baseboard/Fan Forced, temperature offset, °C/°F); What's New; Delete Device.
- Home settings: Edit Home, Shared Access, Geofencing, Shortcuts, Smart Alerts (master switch, geofence leave/enter, all off).
- Menu: Account, Manage Homes, Add Mysa, Add Schedule, Home Assistants (Google Home, Alexa; HomeKit isn't listed for the Lite), Get Help. There's no usage/energy screen for the Lite.

## The app's own network config
`res/xml/network_security_config.xml` allows cleartext HTTP to `10.0.2.2`, `localhost` and **`192.168.4.1`** (probably the thermostat's setup hotspot).
