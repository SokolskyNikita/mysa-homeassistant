# Mysa cloud API, as used by the Android app 4.20.1

Captured 2026-10-10 from a live session of the Android app (traffic through a local TLS proxy), on an account with one Mysa Baseboard Lite (BB-V2-0-L, firmware 3.17.5.13).
Everything below was actually observed unless marked *not captured*. Account data (email, names, user/home IDs, tokens) is left out on purpose; `{user}`, `{home}`, `{dev}` are placeholders. `{dev}` is the lowercase Wi-Fi MAC without colons.

## Transport
- **HTTPS only.** The app opened no MQTT/AWS IoT connection at all. The thermostat itself still holds its own MQTT session to AWS IoT (:8883); Mysa's backend relays commands over it.
- Hosts: `mysa-backend.mysa.cloud` (almost everything), `app-prod.mysa.cloud` (only `GET /users/pushNotification`; also returned as `regionApiBaseUrl` by bootstrap), `i18n.mysa.cloud` (static country/timezone lists), `chunk.mysa.cloud/track` (analytics).
- Other traffic: Firebase/Crashlytics, customer.io (in-app messaging), SNS `CreatePlatformEndpoint` (push registration).

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
  - `latestTelemetry.isConnected`, `lastConnected`, and `reading`: `roomTemperature`, **`humidity`**, `rawTemperature`, `coreTemperature`, `heatSetpoint`, `effectiveSetpoint`, `mode` (3 while heating mode was on), `dutyCycle`, `onTime`, `rssi`, `freeHeap`, `voltage` (240, configured, not measured), `maxCurrent` 0, `powerConsumed` 0, `instantLoad`.
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
  So the `humidity` value in the state (35–37 %) isn't from a real humidity sensor; don't expose it as one.
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

## Not captured on purpose
- Geofencing and Smart Alerts: both need the phone's location/notification permissions (declined), and on the Lite the alerts are only geofence enter/leave. HA does presence itself.
- Firmware install (`/devices/{dev}/firmware-update`), Delete Device, Shared Access invites: irreversible or they email people.

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
