# Mysa cloud API (Android app 4.20.1)

This documents the HTTPS API the Mysa Android app uses, as the backing reference for the `mysa_local` Home Assistant integration's cloud client.

Most of it was captured live from app 4.20.1 running through a local TLS proxy, on an account with one Mysa Baseboard Lite (`BB-V2-0-L`, firmware `3.17.5.13`). Sections marked **static analysis** come from decompiling the app bundle (`hermes-dec`), not from live traffic. Items marked *not captured* are defined in the code but were never called.

The Baseboard Lite is the only model tested on hardware. Other models (Mini-Split AC, Baseboard V3, Central HVAC, Climate Sensor), remote sensors and MQTT are documented from the app's code.

Placeholders:
- `{dev}` — the thermostat's Wi-Fi MAC, lowercase, no colons
- `{home}` — a home ID
- `{user}` — a user ID

## Transport and hosts

The app talks over HTTPS only. It opened no MQTT or AWS IoT connection at all. The thermostat keeps its own MQTT session to AWS IoT on port 8883, and Mysa's backend relays commands to it over that session, but the app never touches MQTT directly.

Hosts:
- `mysa-backend.mysa.cloud` — almost every call
- `app-prod.mysa.cloud` — `GET /users/pushNotification`, and the host bootstrap reports as `regionApiBaseUrl`
- `i18n.mysa.cloud` — static country and timezone lists
- `chunk.mysa.cloud/track` — analytics

Other outbound traffic: Firebase and Crashlytics, customer.io (in-app messaging), and SNS `CreatePlatformEndpoint` for push registration.

### Not usable from a web page (CORS)

Checked by sending browser-style preflights (`Authorization` and `Content-Type` only) from several origins, including Mysa's own `https://app.mysa.cloud`.

- `mysa-backend`: every preflight returns 404 with no CORS headers, and real responses carry no `Access-Control-Allow-Origin`.
- `app-prod`: preflights for its own routes (for example `/devices`) pass, with `Access-Control-Allow-Origin: *` and methods `OPTIONS, GET, POST`. But the real responses have no `Access-Control-Allow-Origin`, so a page can send requests and still can't read the answers. The `mysa-backend`-only routes (`/pairing/*`, `/state/*`, bootstrap) don't exist on `app-prod` (403).
- Cognito (`cognito-idp`) allows any origin, so signing in from a page works. Every API call after it fails to read.

Anything browser-based (like a Wi-Fi setup page) needs a proxy or server to make the Mysa calls. See [Newer models](#newer-models-baseboard-v3-central-hvac-climate-sensor) for the options considered.

## Authentication (AWS Cognito, us-east-1)

These IDs are baked into the app, not user secrets:
- User pool `us-east-1_GUFWfhI7g`
- App client `6cktj934gasnc72f7jo2cmf6rt` (no client secret)
- Identity pool `us-east-1:e27f246e-ad23-41ce-ac80-eda86f8be424`

Sign-in uses SRP: `InitiateAuth` (`USER_SRP_AUTH`) → `PASSWORD_VERIFIER` challenge → `RespondToAuthChallenge` (`PASSWORD_CLAIM_SIGNATURE`, `PASSWORD_CLAIM_SECRET_BLOCK`, `TIMESTAMP`, `USERNAME`). The result is an `IdToken`, `AccessToken`, `RefreshToken` and `ExpiresIn` of 3600. There's no device tracking (`NewDeviceMetadata` is absent).

Renewal: once the ID token expires the app calls `GetTokensFromRefreshToken` with `{"ClientId", "RefreshToken"}`. The answer has a fresh `IdToken` and `AccessToken` (`ExpiresIn` 3600) and no new refresh token, so refresh tokens aren't rotated.

Request shape:
- Cognito calls go to `https://cognito-idp.us-east-1.amazonaws.com/` with `X-Amz-Target: AWSCognitoIdentityProviderService.<Action>` and `Content-Type: application/x-amz-json-1.1`. No AWS signature is needed for this public client.
- Every Mysa API call sends the **raw Cognito ID token** in the `authorization:` header, with no `Bearer` prefix.

The identity pool (`GetId` plus `GetCredentialsForIdentity`) hands out temporary AWS keys. The app used them only to sign the SNS push registration. An HTTPS-only integration never needs them.

## Reading state

Two reads return the shadow:
- `POST /state/batch` with `{"deviceIds":["{dev}"]}` → `{"{dev}": {"data": <state>}}`. The app polls this every ~2.9 s while open.
- `GET /state/{dev}` → `<state>`. The app reads it twice right after each write.

`GET /state` reads Mysa's **stored** copy only. It can be stale: in one test `isConnected` was `true` while the latest reading was 305 s old.

### Shadow structure

The shadow is AWS-IoT style: `desired` is the last command sent, `reported` is what the thermostat confirmed, and timestamps are Unix seconds.

- `latestTelemetry` — `isConnected`, `lastConnected`, and `reading`:
  - `roomTemperature`, `humidity`, `rawTemperature`, `coreTemperature`
  - `heatSetpoint`, `effectiveSetpoint`
  - `mode` (3 while heating was on — different numbering from `modes.mode`, see [Enumerations](#enumerations))
  - `dutyCycle` (heater relay share of the last reading, 0 = off), `onTime`
  - `rssi`, `freeHeap`
  - `voltage` (240, configured not measured), `maxCurrent` 0, `powerConsumed` 0, `instantLoad`
- `targetHeat` — `{desired,reported}.setpoint`, plus `reported.lockoutMin`/`lockoutMax` (hardware setpoint limits, 5/30)
- `modes` — `{desired,reported}.mode` (4 = heat here)
- `physicalInterface` — `lockout` (button lock), `activeIntensity` (wake brightness %), `idleIntensity` (sleep brightness %), `format` ("C"), `intensityMode`
- `identity.reported` — `family` "BB", `model`, `fw`
- `tracking.reported.ambientOffset` — temperature offset
- `power.reported` — `voltage`, `current`, `wattage`, `dutyCycle` (all 0 on the Lite: no current sensor)
- `bbConfig.reported.controlType` — heater type (8 = Fan Forced, 9 = Baseboard; this unit is 8)
- `diagnostics.reported` — `freeHeap`, `drState` {`inEvent`, `state`}

### Live mode

By default the thermostat reports slowly: readings drift to ~40 s apart and can stop for minutes. Live mode speeds it to a reading every ~5 s.

```
POST /mysa-legacy/stream/home/{home}
{"timeout": 300}
→ {"message", "streamedDeviceIds", "skippedDeviceIds", "failedDeviceIds"}
```

The app sends this on open and then every ~4.2 min. Live mode only makes the thermostats report faster; the app still polls `/state/batch` for the actual data.

**Live mode doubles as the one usable cloud liveness ping.** Send it with a short timeout and poll `/state` until `latestTelemetry.reading.timestamp` moves:

```
POST /mysa-legacy/stream/home/{home}  {"timeout": 20}
# then poll GET /state until reading.timestamp advances
```

The first fresh reading arrives about 1 s after the request, and readings stop once the short timeout expires. `streamedDeviceIds` by itself proves nothing; wait for the reading to move.

Two things that are **not** a ping:
- `GET /state`, which returns the stored copy (stale, as above).
- Same-value writes. Writing `ambientOffset` 0 → 0, or an unchanged brightness pair, both return 200 but never move `reported.timestamp`. Only a real change is acknowledged.

The true no-op is the legacy MQTT echo (MsgType 6, EventType `0xFFFFFFFF`, EchoID 418 → MsgType 5), but app accounts can't open an MQTT session (see [MQTT](#mqtt-aws-iot)).

## Writing state: `POST /state/{dev}/update`

Body is `{"source":3, <section>: {<fields>}}`. The reply is `{"message":"Successfully updated state for device {dev}"}`. Each change below showed up in `reported` within about a second.

| Change | Body |
|---|---|
| Setpoint | `{"source":3,"targetHeat":{"setpoint":22.5}}` |
| Button lock on / off | `{"source":3,"physicalInterface":{"lockout":3}}` / `{"lockout":0}` |
| Brightness, wake / sleep % | `{"source":3,"physicalInterface":{"activeIntensity":90,"idleIntensity":50}}` (the app always sends both) |
| Mode off / heat | `{"source":3,"modes":{"mode":0}}` / `{"mode":4}` |
| Hardware setpoint limits | `{"source":3,"targetHeat":{"lockoutMax":29.5}}`, `{"targetHeat":{"lockoutMin":5.5}}` (0.5 steps) |
| Temperature offset | `{"source":3,"tracking":{"ambientOffset":-0.5}}` (−5…+5, 0.5 steps) |

## Writing settings: `POST /devices/{dev}/`

Note the trailing slash. Some settings still go through the older device record. The reply echoes the body.

| Change | Body | Notes |
|---|---|---|
| °C / °F | `{"Format":"fahrenheit"}` / `"celsius"` | Reaches the thermostat; HA's HomeKit units entity flips within seconds. `/state` `physicalInterface.format` stayed "C". |
| Heater type | `{"DutyCycleOpt":9}` (Baseboard) / `8` (Fan Forced) | `/state` `bbConfig.controlType` didn't change within 30 s. |
| Rename | `{"Name":"Bedroom"}` | |
| Time zone | `{"TimeZone":"America/Los_Angeles"}` | IANA name. |

## Schedules and holds

**Create** — `POST /schedule` → 201 `{"scheduleId":…}`:
```json
{"name":"Schedule-{dev}","deviceType":"BB","homeId":"{home}","reusable":false,
 "deviceIds":["{dev}"],
 "events":[{"executionTime":"20:45:00","dayOfWeek":"6","clientKey":"App-4.20.1",
            "state":{"targetHeat":{"setpoint":22},"modes":{"mode":4}}}]}
```
`dayOfWeek` is 0 = Sunday … 6 = Saturday. Times are local to the device's time zone.

**Read** — `GET /schedule?homeId={home}&includeLegacyBaseboard=true&includeLegacyAc=true&includeLegacyInfloor=true` → a list of `{id, name, reusable, homeId, deviceType, deviceIds, events[], createdAt, updatedAt}`. 404 when there's none.

**Delete** — `DELETE /schedule/{scheduleId}/attach/{dev}` → "Schedule unattached from device and deleted".

Editing an existing schedule is *not captured*; the route list has `UPDATE_SCHEDULE /schedule/`.

### Holds

With a schedule active, a plain setpoint change via `/state/{dev}/update` automatically holds until the next event, and the app shows "Holding until …".

- **Permanent hold** — `POST /schedule/hold` → 201 `{"holdId":…}`:
  ```json
  {"deviceId":"{dev}","holdType":"permanent","startTime":"<ISO UTC>",
   "state":{"modes":{"mode":4},"targetHeat":{"setpoint":22.5}}}
  ```
- **Timed hold** — same body with `"holdType":"temporary"` and `"endTime":"<ISO UTC>"`. The `holdId` stays the same, so this replaces the existing hold.
- **Resume schedule** — `DELETE /schedule/hold/{dev}` → "Device restored to follow its schedule". The setpoint snaps back to the schedule's value.
- **Hold status** — `GET /schedule/hold/{dev}` (404 when there's no hold).

A hold's `startTime` must be in the future.

## Shortcuts (Home / Away / Vacation)

- **Edit** — `PATCH /scene/{home}_away` → 202:
  ```json
  {"DeviceActions":{"{dev}":{"type":3,"mode":4,"setpoint":22,
     "holdTillKey":0,"secToHold":null,"version":"V2"}},"AllDevices":false}
  ```
  The app's UI can't remove a device's command once set. To reset it, PATCH `{"DeviceActions":{},"AllDevices":true}` directly.
- **Apply** — `POST /homes/shortcut/apply/{home}_away` with `{"Timestamp":<unix s>}` → `Ok`.

## Read-only and history endpoints

### Capabilities: `GET /capabilities/{dev}`

The app appends `?includeLegacyBaseboard=true&includeLegacyAc=true&includeLegacyInfloor=true`, but it works without. For the Lite it returns a full capability document:
- Control: mode off/heat; setpoint 5–30 in 0.5 steps; `controlType` 8/9; offset −5…+5; lockout 0/3; unit C/F.
- Sensors: `humiditySensor`, `proximitySensor` and `ambientLightSensor` all `false`.
- Connectivity: homekit, googleHome and alexa `true`; matter `false`.
- Smart features: scheduling, geofence and shortcuts `true`; energyReporting, earlyOn, zoning and savedSchedules `false`.

### Humidity (real despite the capability flag)

The capability doc says `humiditySensor: false`, but the Lite's `humidity` reading (35–37 %) is genuine. It tracks dew point correctly against a reference sensor in the same room, and it's compensated for the board's self-heating (uncompensated, the 27 °C `rawTemperature` would read about 25 %). Mysa Local exposes it. For a history series, use the usage endpoint's ~30 s `rawReadings` `h` values.

### Legacy device and state records

- **`GET /devices`** (legacy settings record): `ButtonState`, `Format`, `DutyCycleOpt`, `HeaterType` "radiant", `MinBrightness`/`MaxBrightness`, `MinSetpoint`/`MaxSetpoint`, `TimeZone`, `UserTempOffset`, `ecoMode` 1, `Animation` "off", `ButtonThreshold` and other button-timing values, `LastPaired`, `Voltage`, `MaxCurrent`.
- **`GET app-prod/devices/state`** (legacy state, each key as value plus timestamp): `CorrectedTemp`, `SensorTemp`, `HeatSink`, `Humidity`, `SetPoint`, `Duty` (0/1 relay), `OnTime`/`OffTime`, `Lock`, `Brightness`, `Mode`, `TstatMode`, `ScheduleMode`, `HoldTime`, `Rssi`, `Connected`, `Current`, `Voltage`, `FreeHeap`.

### Usage and heat runtime: `POST /telemetry/usage/{dev}`

The app hides this screen for the Lite (`handleUsageFetch` returns early), but the endpoint works with a direct call:
```json
{"timezone":"America/Los_Angeles","start":<unix s>,"end":<unix s>,
 "summary":{"oH1":"sum","k":"sum","t":"average","h":"average","n":"sum"},
 "aggregatedReadings":["oH1","k","t","h","n"],
 "rawReadings":["t","c","h","i","s","hs"]}
```
The app sends `rawReadings` only for a one-day range. The response is a list per day:
- `summary.a` = {`oH1` heat on-time in seconds, `t` average temperature, `h` average humidity, `n` sample count}
- `aggregatedReadings` = 30-minute buckets of the same
- `rawReadings` = ~30 s samples {`t` time, `c` room °C, `h` humidity, `s`/`hs` setpoint}

`k` (energy) is absent because the Lite has no current sensor. Use it for heater runtime → kWh (with the heater's wattage) and for back-filling temperature history.

### State change log: `GET /state/history/{dev}`

```
GET /state/history/{dev}?startTime=<unix s>&endTime=<unix s>
```
Integers, seconds. Returns, per field (`heatSetpoint`, `coolSetpoint`, `mode`, …), a list of `{timestamp, value, source}`. `source` is `device` (thermostat buttons), `app`, `schedule` or `shortcuts`. The app defines this route but never calls it. Use it to tell whether a change came from the buttons, a schedule or a shortcut.

### Other read-only endpoints that work for the Lite

- `GET /devices/update_available/{dev}` → `{"update":false,"installedVersion":"3.17.5.13","allowedVersion":"3.17.5.13"}`. See [Firmware](#firmware-updates).
- `GET /devices/{dev}/telemetry_alerts` → `{"Items":[]}`.
- `GET /smart-early-on/{dev}/availability` → `available:false`, `model-not-eligible:BB-V2-0-L` (server-enforced).
- `GET /homes` → home with `Zones: []`.
- `POST /energy/v3/{home}/home` → 404 (whole-home energy, not for the Lite).

## Adding and removing a thermostat

**Static analysis** of app bundles 4.20.1 and 3.54.1, plus live tests for the claim, the delete and `/devices/purgeadd`.

### Add (claim): `POST /devices`

During setup in 4.20.1: Wi-Fi setup over Bluetooth or the thermostat's own access point → `CONNECTED` → claim (`ADDING_TO_ACCOUNT`) → connection check (`VERIFYING_CONNECTION`; for the Lite a legacy-echo check that gives up after 4 min) → firmware update → done. The app claims the thermostat as soon as it has handed over the Wi-Fi details, before the cloud has seen it online.

4.20.1 sends the claim to `mysa-backend`, 3.54.1 to `app-prod`. Lite body (4.20.1):
```json
{"Id": "{dev}", "Name": "<name chosen in the app, else {dev}>", "Model": "BB-V2-0-L",
 "Home": "{home}", "TimeZone": "<the phone's IANA time zone>", "LastPaired": <unix s>,
 "Animation": "off", "ecoMode": 1, "Format": "celsius", "schedGlobalOffset": 0,
 "DutyCycleOpt": 9}
```
- `Id` is the Wi-Fi MAC (lowercase, no colons). Nothing in the body proves you hold the thermostat; it's only the ID.
- `Format` follows the user's °C/°F preference. `DutyCycleOpt` 9 is Baseboard, so a freshly added Lite starts as Baseboard, not Fan Forced.
- Per-model variants:
  - AC adds `SetupRequired: true`.
  - In-floor adds floor-sensor defaults (`FloorOhms` 10, `FloorType`, `IsMysaFloorSensor`, `MaxFloorTemp`, `MaxSetpoint`, `Tracking`).
  - BB-V1 uses `DutyCycleOpt` 0.
- 3.54.1 sent only `Id, TimeZone, Name, LastPaired, Home, Model`, plus `SetupRequired` for AC and `schedGlobalOffset: 0` for the Lite. `Home` was `"PROPERTY_HOME"` for property-manager accounts.

`DUTY_CYCLE_OPT` values: 0 BB1 baseboard, 4 BB1 radiant, 5/6/7 BB1 fan forced short/medium/long, 8 BB2 fan forced, 9 BB2 baseboard.

The app's HTTP client counts **409 as a success** (`{ok: true, statusCode: 409}`), like 202 and 204.

Outcomes:
- **2xx** — added. The app reloads the account (`fetchEssentialData`) and creates the home's default shortcuts if it has none.
- **409 — the thermostat is already on *this* account** (confirmed live). The response body is the existing device record. A 409 writes nothing, even with a changed `LastPaired`.
  - 3.54.1 showed a dialog with Keep (use the record as is) and Overwrite (re-send the body to `POST /devices/purgeadd` on `app-prod`, which replaces the record).
  - 4.20.1 has no dialog and no `purgeadd`. On 409 it keeps the old record, only sets `ecoMode: 1` via `POST /devices/{dev}/`, and logs the pairing as "re-paired".
- **On another account** — if the error text contains "User is not allowed to access the specified home", the app shows a Contact Support message. That's the only ownership check, and the server enforces it.
- "No Internet Connection" retries up to 3 times. Any other error shows "Failed To Add Mysa to Account".

The Lite and other older models need only this claim. [Newer models](#newer-models-baseboard-v3-central-hvac-climate-sensor) need more.

### Remove: `DELETE /devices/{dev}`

No body. The route `DELETE_DEVICE` is one of the app's legacy routes, which go to `app-prod` unless the account's `MONOLITH_API_ACCESS` flag is on, which sends them to `mysa-backend`. The flag was on when checked, so 4.20.1 uses `mysa-backend`. Both hosts work.

The app's own order (`useDeviceDeletion` in 4.20.1), behind Settings › Delete Device:
1. Only the home's admin may delete. A shared user is refused by the app.
2. Take the thermostat out of every shortcut that lists it.
3. Remove its schedule: old-style (a `Schedule` field on the device record) through `/schedules/v2`, or new-style with `DELETE /schedule/{id}/attach/{dev}`.
4. Delete sensor/HVAC links and clear `GroupName` on grouped devices. The Lite has neither.
5. `DELETE /devices/{dev}`, then drop it from local data.

`DELETE` returns 204 with no body. Afterwards the device is gone from `GET /devices` and bootstrap `AllowedDevices`; `GET /state/{dev}` → 404 "User does not have access to this device"; `/state/batch` → a `PERMISSION_DENIED` error for that ID. The thermostat itself doesn't notice: it stays online with the cloud, and HomeKit control keeps working.

### Delete and re-add semantics

Verified by delete-then-re-add on a Lite that was online and paired to HA over HomeKit:
- Re-adding after a delete returns **201** (a 409 would mean it never left the account).
- The thermostat's live state (setpoint, mode, hardware limits, brightness, lock, offset, `controlType`) comes back intact, along with capabilities and the home's shortcuts. Only the account's record was gone.
- The record fields named in the claim body **overwrite** the stored record. A re-add with the default body (`DutyCycleOpt` 9) changed the stored heater type from Fan Forced to Baseboard, while the thermostat kept reporting `controlType` 8. Fields the body leaves out (brightness range, setpoint limits, button timings, `Voltage`) came back unchanged.

So when re-adding, send the thermostat's real values. The safest body is the 3.54.1 shape (`Id`, `Name`, `Model`, `Home`, `TimeZone`, `LastPaired`, plus `schedGlobalOffset` 0 on a Lite), which leaves stored settings alone.

## Firmware updates

**Static analysis** of 4.20.1; nothing was triggered.

The phone never downloads or pushes a firmware image. The Lite uses the legacy path: the app asks Mysa's cloud to start the update and the thermostat fetches the image itself. The only BLE file transfer anywhere is the pairing credentials blob for newer models, not firmware.

Legacy flow (BB-V1/V2/Lite, In-floor, AC):
1. `GET /devices/update_available/{dev}` → `{update, installedVersion, allowedVersion}`.
2. If `update` isn't false: `POST mysa-backend /devices/{dev}/firmware-update` with no body.
3. Re-check every 20 s until `update === false`. The app gives up after 6 min. The home-screen variant polls `update_available` every 10 s and then reads `GET /devices/firmware`.

In 4.20.1 the app only starts a Lite update right after pairing; the home-screen update sheet never opens for a Lite.

Newer models use `/ota/update/available/{dev}`, `POST /ota/update` with `{deviceIds, versionId, requestSource:"USER", …}`, `GET /ota/update/{requestId}` and `PUT /ota/update/{requestId}/cancel`, plus fleet registration at pairing. The Lite isn't in any fleet: `GET /ota/update/available/{dev}` → 404 "No fleet found containing device".

For an HA `update` entity: the check is verified live; install is the single legacy POST above (untested, irreversible).

The `legacyBaseboardMigration` flags are hard-coded `true` in 4.20.1. That's why the Lite gets `/state`, `/capabilities` and `/schedule` like newer models, while firmware, pairing and fleet stay legacy.

## Enumerations

From the app bundle, 4.20.1.

### Modes (`modes.mode`, `CONTROL_MODE_TYPE`)

| Value | Name | | Value | Name |
|---|---|---|---|---|
| 0 | off | | 6 | preCool |
| 1 | auto | | 7 | fanOnly |
| 3 | cool | | 8 | dry |
| 4 | heat | | 9 | sleep |
| 5 | emergencyHeat | | | |

The capability document uses the name strings. Which section holds a mode's setpoint (`controlModeSetpointMapping`): cool → `targetCool`; heat and emergency heat → `targetHeat`; auto → `targetAuto`.

`latestTelemetry.reading.mode` uses a *different* numbering (3 while the Lite heats), so don't cross-read it against `modes.mode`.

### Fan and swing

- Fan speeds (`CONTROL_FAN_SPEED`): 0 off, 1–7 speed 1–7, 8 sleep, 9 auto. This is **not** what a Mini-Split AC uses; see [Mini-Split AC](#mini-split-ac-fan-speed-and-swing).
- Swing (`CONTROL_SWING`): 0 invalid, 1 off, 2 on, 3 auto, 4–10 positions 1–7, 11 toggle, 12 n/a.

### State sections (`CONTROL_SHADOW_TYPE`)

`latestTelemetry`, `targetHeat`, `targetCool`, `targetAuto`, `modes`, `tracking`, `physicalInterface`, `hvacConfig`, `identity`, `telemetry`.

## Remote temperature tracking (not on the Lite)

Newer models can control on a remote reading ("Linked Sensors": Climate Sensors linked to a thermostat via `GET/POST /linking` with `{sourceId, targetId, weight}`; the menu shows only for central-HVAC thermostats in 4.20.1). The app switches tracking with `{"source":3,"tracking":{"tracking":2,"remoteTTL":480}}` (tracking mode 0 = ambient, 1 = probe, 2 = remote; `DEFAULT_REMOTE_TTL_SECONDS` 480). The cloud feeds the readings itself; the app has no call that writes a temperature.

The Lite refuses it. `/capabilities` reports `sensing.temperature.sensors.remote: false`, `trackingSensor.validValues: ["internal"]`, `hardware.sensors.externalTemperatureProbeConnector: false`, and `GET /linking?targetId={dev}` → `[]`. Writing it anyway lands in `tracking.desired`, but the thermostat never reports `tracking` back.

The closest working substitute is `ambientOffset` (−5…+5, 0.5 steps), which shifts both the control temperature and the reported room temperature. Reset with `{"tracking":{"tracking":0,"ambientOffset":0}}`.

## Mini-Split AC fan speed and swing

**Static analysis** of 4.20.1, not tested on an AC. 4.20.1 hard-codes `legacyAcMigration` on, so an AC uses `/state` and `/capabilities` like the Lite, read with `includeLegacyAc=true`.

### Fan speed (`modes.fan_mode`)

The app displays the AC remote's own `Fan` values (1 auto, 2 low, 3 speed 1, 4 medium, 5 speed 2, 6 high, 7 speed 3, 8–11 speeds 4–7, 12 sleep) and converts them to the shadow's values before writing (`AC_NATIVE_FAN_TO_SHADOW_FAN_MODE`):

| Shadow `fan_mode` | Meaning |
|---|---|
| 0 | auto |
| 1 | low |
| 2 | medium |
| 3 | high |
| 4–10 | speeds 1–7 |
| 11 | sleep |

Write (`setControlFanSpeed`, AC branch of `useFanService`): `{"source":3,"modes":{"fan_mode":<0–11>,"mode":<current mode>}}`. Other models with capabilities send `fan_mode` alone, in `CONTROL_FAN_SPEED` numbers.

This table was read from register values in the decompiled module, so confirm it live. Mysa_HA's MQTT `fn` values (auto 1, low 2, medium 3, high 4, sleep 12) are exactly these plus one, which agrees.

### Swing

`modes.verticalSwingState` / `modes.horizontalSwingState`, using the `CONTROL_SWING` values above (1 off, 2 on, 3 auto, 4–10 positions). Write (`setControlSwing`): `{"source":3,"modes":{"verticalSwingState":<v>}}`, no conversion and no mode. Remotes with only a toggle button use `verticalSwingToggle` / `horizontalSwingToggle` instead (`SWING_TOGGLE_MODES` 1 off, 2 on). Mysa Local doesn't drive the toggle variants.

### What the remote supports

Comes from the settings record (`GET /devices`), not `/capabilities`:
- `SupportedModes`, keyed by `DEVICE_MODE_TYPES` (`"1"` off, `"2"` auto, `"3"` heat, `"4"` cool, `"5"` fan, `"6"` dry). Each entry has `temperatures`, `fanSpeeds` (`Fan` values), `verticalSwing` and `horizontalSwing` (`CONTROL_SWING` values; 11 = toggle, 12 = n/a).
- `SupportedKeys` / `modifiedKeys`: the remote's IR key IDs. When `modifiedKeys` is non-empty (the user customized the remote), the app keeps only the fan speeds whose key is in it (auto 8, low/speed 1 → 9, medium/speed 2 → 10, high/speed 3 → 11, speeds 4–7 → 16–19, sleep 20) and drops a swing direction with none of its keys (vertical 12/39/40/47, horizontal 15/41/42).
- `SupportedCapsVersion` `"1.2"` marks a newer remote (numbered speeds).

The app hides fan speed while the AC is off.

## Newer models: Baseboard V3, Central HVAC, Climate Sensor

**Static analysis** of 4.20.1; none were available to test. This integration doesn't add them, and the cloud client refuses them (`ValueError`).

Wi-Fi details alone aren't enough for these to join. The app also sends each one a credential file from Mysa's cloud over Bluetooth, then registers it for updates and finalizes the pairing.

Setup order, as far as the code shows:
1. Over Bluetooth: the same Wi-Fi steps as older models, plus the credential file. The file comes from `POST mysa-backend /pairing/credentials {"deviceId", "model", "serialNumber"}`, whose `credentials` field is base64. The serial number is read over Bluetooth (Device Information characteristic `2a25`).
2. The thermostat reports "register device" over Bluetooth. The app registers it with an update fleet: `POST /ota/fleet/devices {"deviceIds": ["{dev}"], "fleetName": "<type>-prod"}`, where `<type>-prod` is `bb3-prod`, `lv-prod` (Central HVAC) or `cs-prod`.
3. The claim: `POST /devices` with base fields only (no `DutyCycleOpt`/`schedGlobalOffset`).
4. Connection check: wait up to 5 min for `GET /state/{dev}`'s `identity` section to show a newer timestamp.
5. `POST /pairing/finalize {"deviceId"}`.
6. Firmware through the new OTA system (see [Firmware](#firmware-updates)).

Bluetooth credential transfer — service `ecaa050b-0da4-e394-d64c-5d08e47cb41d`:
- Control characteristic `708aa387-184f-fca4-6b4e-eef896ec9bb5`. Commands: 0 none, 1 start, 2 cancel.
- Data characteristic `81838135-4ca1-96b1-1b47-6e96772fef72`. The app sends the decoded file in chunks sized to the connection.
- Status: 0 idle, 1 downloading.
- The exact start payload, chunk size and completion signal aren't extracted.

Local control is Matter over Wi-Fi:
- After setup, `GET /matter/qr/{dev}` returns `{qrCode, pairingCode}`.
- The user pastes the code into any Matter app's "add device". This pairs a device already on the network; the app never mentions Thread.
- Unlike the HomeKit code, this code isn't printed on the thermostat, so the first Matter pairing needs the cloud once. Control is local after that.
- In HA this would go through HA's own Matter integration, not this one.

A browser Wi-Fi setup page can't do the credential step, because it can't call Mysa's API (CORS, see [Transport](#transport-and-hosts)). It would need something outside the browser: a proxy such as a Cloudflare Worker that forwards the pairing calls and adds CORS headers, a page served by HA that uses the sign-in it already holds, or a browser extension.

## MQTT (AWS IoT)

Tried Mysa_HA's route with a regular app account: temporary AWS keys from `GetCredentialsForIdentity`, a SigV4-signed WebSocket to `a3q27gia9qg3zy-ats.iot.us-east-1.amazonaws.com/mqtt`. The TLS and WebSocket upgrade succeed (HTTP 101, subprotocol `mqtt`), so the signature is accepted, but the broker drops the connection right after the MQTT CONNECT (no CONNACK). This identity is no longer allowed to connect.

Treat MQTT as unavailable for app accounts. The integration uses HTTPS (cloud) plus HomeKit (local).

## Feature flags and network diagnostics

`GET mysa-backend /feature-flags/features/check?versionNumber=4.20.1` → `{"features": {"<NAME>": {"value": true|false}, …}}`. The app loads these at start. All were `true` except `HIDE_MATTER_IN_APP` (`false`).

Host routing flags:
- `MONOLITH_API_ACCESS` sends the app's legacy routes to `mysa-backend` instead of `app-prod`.
- `DISABLE_LEGACY_FALLBACK` turns off retrying them on `app-prod`.
- With both on, the app talks to `mysa-backend` only.

Migration flags `LEGACY_BASEBOARD_MIGRATION`, `LEGACY_AC_MIGRATION` and `LEGACY_INFLOOR_MIGRATION` move older models onto `/state`, `/capabilities` and `/schedule`.

Other-model flags: Central HVAC (`LV_ACCESS`, `LV_AUTOMODE_FW`, `LV_ADAPTIVE_BRIGHTNESS_FW`, `LV_STAGED_AUX_HEAT_FW`, `LV_FREEZE_PROTECTION_ENABLE_FW`, `LV_TEMP_DIFFERENTIAL_FW`, `LV_EXTERNAL_PROBE_FW`), Baseboard V3 (`BB3_SMART_EARLY_ON_ACCESS`, `ENROLL_BB3_DR`), and general (`SMART_EARLY_ON_V2_ACCESS`, `ADAPTIVE_BRIGHTNESS_ACCESS`, `STAGED_AUX_HEAT_ACCESS`, `DR_ENROLMENT_WEB_FLOW_ACCESS`).

Network diagnostics don't touch the thermostat. `POST /network-diagnostics/test` uploads results the phone computed on its own (`WifiConnection`, `SpeedTest`, `MysaConnectivity`), and `GET /network-diagnostics/history` reads past uploads back. Neither is worth calling for an integration.

## Other endpoints seen

All GET unless noted.
- `/mobile/bootstrap?versionNumber=4.20.1` — `versionNumber` is required (400 `FST_ERR_VALIDATION` without it). Returns the user record (`AllowedDevices`, `AllowedHomes`, Cognito attributes), `primaryHomeId`, `activeHomeId`, `regionApiBaseUrl`.
- `/homes/{home}/scenes` — Home/Away/Vacation shortcuts (`Id` = `{home}_home|_away|_vacation`). Editing and applying: [Shortcuts](#shortcuts-home--away--vacation).
- `/homes/{home}/savedSchedule` → `{"SavedSchedules":[]}`; `/schedule/compatibility/{dev}` → `[]`.
- `/homes/{home}/geofences`, `/homes/reminders/{home}`, `/homes/{home}/users`, `/invite/users/{user}`, `/devices/{dev}/drstate`, `/communication-preferences/`, `/preferences/User_{user}_Home_{home}…`.
- `POST /energy/setpoints/device/{dev}` with `{"Scope":"Day","Timestamp":…,"PhoneTimezone":"America/Los_Angeles"}` → setpoint change history (`Prev`, `Next`, `Source`, `SourceTime`).
- `PATCH /users/{user}` with `{LastAppVersion, OperatingSystem, DeviceType, LanguagePreference}` on sign-in; `POST /users/pushNotification` (push token).

## App network config

`res/xml/network_security_config.xml` allows cleartext HTTP to `10.0.2.2`, `localhost` and `192.168.4.1` (the thermostat's setup hotspot).
