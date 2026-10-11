# Mysa cloud API

Mysa Local uses Mysa's HTTPS API for cloud control and HomeKit for local control. The cloud client follows Android app 4.20.1. Older app 3.54.1 used MQTT directly; its protocol explains some of the legacy fields and setup behavior that remain in the service.

We tested the API on a Baseboard Lite (`BB-V2-0-L`, firmware `3.17.5.13`) on October 10–11, 2026. That is the only model tested on hardware. The AC implementation comes from decompilation and tests with constructed data. Baseboard V3, Central HVAC and Climate Sensor setup is documented from app code and is not implemented here.

Evidence in this document has three sources:

- App traffic and direct API experiments on the Lite.
- Static analysis of the 4.20.1 and 3.54.1 Android bundles.
- The Python client, its Home Assistant callers and tests.

An implemented method or passing fixture test does not establish that a feature works on hardware. Sections below identify app-only behavior and untested operations where that distinction affects an implementation.

Paths are relative to `https://mysa-backend.mysa.cloud` unless another host is given. `{dev}` is the Wi-Fi MAC in lowercase with no colons, `{home}` is a home ID and `{user}` is a user ID. These are separate from the HomeKit accessory ID. Temperatures in the control examples are Celsius; timestamps are Unix seconds unless stated otherwise.

- [Connection and authentication](#connection-and-authentication)
- [Account discovery and capabilities](#account-discovery-and-capabilities)
- [Reading and writing thermostat state](#reading-and-writing-thermostat-state)
- [Schedules, holds and shortcuts](#schedules-holds-and-shortcuts)
- [Telemetry and history](#telemetry-and-history)
- [Model-specific controls](#model-specific-controls)
- [Adding and removing devices](#adding-and-removing-devices)
- [Firmware](#firmware)
- [Legacy MQTT](#legacy-mqtt)
- [Other app endpoints](#other-app-endpoints)
- [Python implementation and evidence](#python-implementation-and-evidence)

## Connection and authentication

### Hosts and app versions

App 4.20.1 used HTTPS for every captured cloud request. It opened no MQTT connection and its decompiled bundle has no MQTT client. The thermostat itself keeps a TLS MQTT connection to AWS IoT on port 8883; Mysa's backend relays commands over that connection.

| Host | Use |
|---|---|
| `mysa-backend.mysa.cloud` | State, capabilities, schedules, bootstrap and most legacy API calls in the captured 4.20.1 session |
| `app-prod.mysa.cloud` | Older REST API; bootstrap still reports it as `regionApiBaseUrl`; the capture also used it for `GET /users/pushNotification` |
| `cognito-idp.us-east-1.amazonaws.com` | Sign-in and token refresh |
| `i18n.mysa.cloud` | Static country and time-zone lists |
| `chunk.mysa.cloud/track` | Analytics |

The app also contacted Firebase, Crashlytics, customer.io and SNS for app services and push registration.

The 4.20.1 host selection depends on feature flags. `MONOLITH_API_ACCESS` moves legacy routes to `mysa-backend`; `DISABLE_LEGACY_FALLBACK` disables retrying those routes on `app-prod`. Both were enabled in the captured account. This does not mean every request uses the same host.

The Python client uses `mysa-backend` directly. Its only `app-prod` method is `replace_device()`, which calls the older `/devices/purgeadd` route.

### Cognito sign-in

These public app identifiers are the same in both app versions:

| Setting | Value |
|---|---|
| AWS region | `us-east-1` |
| User pool | `us-east-1_GUFWfhI7g` |
| App client | `6cktj934gasnc72f7jo2cmf6rt` |
| Identity pool | `us-east-1:e27f246e-ad23-41ce-ac80-eda86f8be424` |

The app client has no client secret. Cognito requests go to `https://cognito-idp.us-east-1.amazonaws.com/` with:

```http
X-Amz-Target: AWSCognitoIdentityProviderService.<Action>
Content-Type: application/x-amz-json-1.1
```

No AWS request signature is required for these user-pool calls. Sign-in uses `InitiateAuth` with `AuthFlow: "USER_SRP_AUTH"` and `AuthParameters` containing `USERNAME` and `SRP_A`. A `PASSWORD_VERIFIER` challenge follows. `RespondToAuthChallenge` sends `PASSWORD_CLAIM_SIGNATURE`, `PASSWORD_CLAIM_SECRET_BLOCK`, `TIMESTAMP` and `USERNAME` as `ChallengeResponses`, plus the challenge session if supplied. The response contains `IdToken`, `AccessToken`, `RefreshToken` and `ExpiresIn: 3600`. The captured sign-in had no `NewDeviceMetadata`.

The Python SRP implementation uses Cognito's 3072-bit group, SHA-256 and a 16-byte derived authentication key. Use the challenge's `USER_ID_FOR_SRP` when computing the response, even when sign-in began with an email address. Cognito also expects an English UTC timestamp with an unpadded day, for example `Fri Jan 2 03:04:05 UTC 2026`. [srp.py](../custom_components/mysa_local/api/srp.py) contains the calculation; [test_srp.py](../tests/api/test_srp.py) checks it against a simulated server and optionally `pycognito`.

Every Mysa API request sends the raw Cognito ID token:

```http
authorization: <IdToken>
accept: application/json
```

There is no `Bearer` prefix. The access token is not the API credential. Python also sends its integration `User-Agent` and adds `content-type: application/json` when the request has a body.

Refresh uses `GetTokensFromRefreshToken` with:

```json
{"ClientId":"6cktj934gasnc72f7jo2cmf6rt","RefreshToken":"<refresh token>"}
```

The observed response supplied fresh ID and access tokens with a one-hour lifetime and no replacement refresh token. [MysaAuth](../custom_components/mysa_local/api/auth.py) keeps the existing refresh token in that case and stores a replacement if one is returned. It refreshes five minutes before ID-token expiry and serializes concurrent refreshes.

The client supports the password-verifier flow only; another challenge raises `MysaUnsupportedChallengeError`. If the user chose to save their password, the integration can sign in again when refresh authentication fails. Otherwise it asks for reauthentication.

The identity pool is a separate AWS credential flow: `GetId` followed by `GetCredentialsForIdentity`. App 4.20.1 used those temporary keys for SNS push registration. The HTTPS client does not need them. App 3.54.1 also used them for [MQTT](#legacy-mqtt).

### HTTP responses and errors

[MysaClient](../custom_components/mysa_local/api/client.py) uses a 20-second request timeout. It accepts any 2xx response, returns `None` for an empty body and accepts text replies when the body is not JSON.

| Response | Python behavior |
|---|---|
| 401 | Force one token refresh and retry once |
| 403, or 401 after that retry | `MysaApiError`; a route or permission failure does not by itself mean the sign-in expired |
| 404 | `MysaNotFoundError`; schedule and hold methods handle their expected empty cases |
| 409 from `POST /devices` | `add_device()` returns `False`: the device is already on the account |
| 429 | `MysaRateLimitError`, with a numeric `Retry-After` value when present |
| 5xx, timeout or transport failure | `MysaConnectionError` |

The app's HTTP wrapper treats 409 as a successful result in general. The Python client handles it specifically for device claims; other non-2xx responses remain errors.

### Browser access

CORS was checked with browser-style preflights containing `Authorization` and `Content-Type`, from several origins including `https://app.mysa.cloud`.

On `mysa-backend`, preflights returned 404 without CORS headers. Actual responses lacked `Access-Control-Allow-Origin` too. On `app-prod`, preflights for its routes, including `/devices`, allowed `*` and `OPTIONS, GET, POST`, but actual responses still lacked `Access-Control-Allow-Origin`. The newer pairing, state and bootstrap routes returned 403 on that host. Cognito allowed browser sign-in, but that did not make the subsequent Mysa responses readable.

A browser client therefore needs a server or extension for the cloud calls. Options considered for the Wi-Fi setup page were an HA-served endpoint using HA's stored sign-in, a proxy that adds CORS headers or a browser extension.

## Account discovery and capabilities

`GET /mobile/bootstrap?versionNumber=4.20.1` returns account data and routing metadata. Omitting `versionNumber` returned 400 with `FST_ERR_VALIDATION`. The response has `meta` and `data` objects: home selection is in `meta.primaryHomeId` / `activeHomeId`; `data.user` contains `Id`, `AllowedDevices`, `AllowedHomes` and Cognito attributes. The bootstrap fixture also records `data.homes` and `data.devices`.

`GET /devices` returns `{"DevicesObj":{"{dev}":{...}}}`. This is the account's settings record. Its fields include `Id`, `Model`, `Name`, `Home`, `TimeZone`, `LastPaired`, `Format`, `DutyCycleOpt`, `HeaterType`, `ButtonState`, brightness and setpoint limits, `UserTempOffset`, `ecoMode`, `Animation`, button timing values, `Voltage` and `MaxCurrent`. The record and the thermostat's reported state can disagree.

`GET /capabilities/{dev}` describes the device's supported features and valid values. The app and Python client append:

```text
?includeLegacyBaseboard=true&includeLegacyAc=true&includeLegacyInfloor=true
```

The Lite request also worked without those parameters. Its response reported:

| Area | Lite values |
|---|---|
| Climate control | Off and heat; setpoint 5–30 °C in 0.5 °C steps; control type 8 or 9 |
| Temperature sensing | Offset −5 to +5 °C in 0.5 °C steps; internal tracking only |
| Interface | Lockout 0 or 3; C/F display units; wake and sleep brightness |
| Hardware sensor flags | Humidity, proximity and ambient light all `false` |
| Connectivity | HomeKit, Google Home and Alexa `true`; Matter `false` |
| Smart features | Scheduling, geofence and shortcuts `true`; energy reporting, Early On, zoning and saved schedules `false` |

The humidity flag conflicts with the [observed readings](#humidity-and-lite-energy-data). It must not be used to discard humidity that the thermostat actually reports.

The Python `Capabilities` parser reads `features.climateControl`, `features.sensing`, `features.interface`, `features.connectivity`, `features.smart`, `hardware.sensors` and `system`. It ignores controls marked `userControllable: false`, derives ranges from `validValues` and retains the original response in `raw`.

App 4.20.1 hard-codes the legacy baseboard, AC and in-floor migration selectors on. Older models consequently use `/state`, `/capabilities` and `/schedule`, while their provisioning and firmware still use the legacy paths. A capability document does not imply support for the newer credential or OTA flows.

## Reading and writing thermostat state

### State reads

| Request | Response |
|---|---|
| `POST /state/batch` with `{"deviceIds":["{dev}"]}` | `{"{dev}":{"data":<state>}}`; individual IDs can instead have an error |
| `GET /state/{dev}` | The state object directly |

App 4.20.1 polled the batch endpoint about every 2.9 seconds while open and read the individual endpoint twice after each write. Python `get_states()` keeps entries with a `data` object and skips per-device errors. The coordinator replaces its state map with that result, so an errored device loses its previous cached state.

Both endpoints return Mysa's stored state. One read showed `isConnected: true` with telemetry already 305 seconds old. Check `latestTelemetry.reading.timestamp` when freshness matters. HA currently considers the cloud path usable after a successful poll when `isConnected` is not false; it does not impose a telemetry-age limit.

### State structure

Most control sections contain `desired` and `reported` objects with their own timestamps. `desired` records a requested change; `reported` records what the thermostat confirmed. A field accepted into `desired` can remain there indefinitely if the device does not support it.

This abbreviated example shows the shape, not a complete response:

```json
{
  "latestTelemetry": {
    "isConnected": true,
    "lastConnected": 1791700000,
    "reading": {
      "timestamp": 1791700000,
      "roomTemperature": 21.4,
      "humidity": 40,
      "heatSetpoint": 21.5,
      "mode": 3,
      "dutyCycle": 0.4
    }
  },
  "targetHeat": {
    "desired": {"timestamp": 1791699000, "setpoint": 21.5},
    "reported": {"timestamp": 1791699002, "setpoint": 21.5, "lockoutMin": 5, "lockoutMax": 30}
  },
  "modes": {
    "reported": {"timestamp": 1791699002, "mode": 4}
  }
}
```

| Section | Fields and interpretation |
|---|---|
| `latestTelemetry.reading` | `roomTemperature`, `humidity`, `rawTemperature`, `coreTemperature`, `heatSetpoint`, `effectiveSetpoint`, `mode`, `dutyCycle`, `onTime`, `rssi`, `freeHeap`, `voltage`, `maxCurrent`, `powerConsumed`, `instantLoad` |
| `targetHeat` / `targetCool` | `setpoint`; the Lite's `targetHeat.reported` also has hardware limits `lockoutMin` / `lockoutMax` |
| `modes` | `mode`; AC also uses `fan_mode` and swing fields |
| `physicalInterface` | `lockout`, `activeIntensity`, `idleIntensity`, `format`, `intensityMode` |
| `identity.reported` | `family` (`"BB"` on the Lite), `model`, `fw` |
| `tracking.reported` | `ambientOffset`; other models can have tracking controls |
| `bbConfig.reported` | `controlType`: 8 fan forced, 9 baseboard |
| `power.reported` | `voltage`, `current`, `wattage`, `dutyCycle`; all were zero on the Lite |
| `diagnostics.reported` | `freeHeap`; demand-response `drState` was also recorded here |
| `drState` | `inEvent`, `state`; a top-level object in the fixture, used by the Python parser |

Telemetry voltage was 240 V, a configured value. The Lite has no current sensor; zero power fields do not show whether its heater is running. Python uses telemetry `dutyCycle > 0` for heating. The value is the relay's share of the reading interval, so it can be fractional.

Do not apply the `modes.mode` enum to `latestTelemetry.reading.mode`. On the Lite, heating appeared as telemetry mode 3 while `modes.reported.mode` was 4.

The app's `CONTROL_SHADOW_TYPE` names include `latestTelemetry`, `targetHeat`, `targetCool`, `targetAuto`, `modes`, `tracking`, `physicalInterface`, `hvacConfig`, `identity` and `telemetry`. The response can contain further sections, as the table shows.

[DeviceState](../custom_components/mysa_local/api/models.py) reads `reported` only for split sections. It does not fall back to `desired` or telemetry for mode and setpoints. Missing or unknown values remain `None`; the raw response is retained.

### Live mode and liveness

Without live mode, Lite readings drifted to roughly 40 seconds apart and sometimes stopped for minutes. This request made the home's thermostats report about every five seconds:

```http
POST /mysa-legacy/stream/home/{home}
Content-Type: application/json

{"timeout":300}
```

The response has `message`, `streamedDeviceIds`, `skippedDeviceIds` and `failedDeviceIds`. The app renewed it about every 4.2 minutes. Live mode changes the thermostat's reporting rate; the client still has to poll state.

For a cloud liveness check, record the reading timestamp, request a short stream, for example `{"timeout":20}`, then poll `GET /state/{dev}` until that timestamp advances. The first fresh reading arrived about one second after the test request. Readings stopped when the short stream expired. A returned ID in `streamedDeviceIds` alone does not confirm that the thermostat responded.

Same-value writes are also unsuitable as a ping. Writing an unchanged offset or brightness pair returned 200 without advancing `reported.timestamp`.

### State writes

`POST /state/{dev}/update` takes `{"source":3,<section>:{<fields>}}`. Source 3 is the app source; changes appear as `"app"` in state history. The success reply was `{"message":"Successfully updated state for device {dev}"}`.

Each of these real changes reached the Lite's `reported` state within about one second:

| Change | Complete request body |
|---|---|
| Heat setpoint | `{"source":3,"targetHeat":{"setpoint":22.5}}` |
| Heat / off | `{"source":3,"modes":{"mode":4}}` / `{"source":3,"modes":{"mode":0}}` |
| Lock / unlock buttons | `{"source":3,"physicalInterface":{"lockout":3}}` / `{"source":3,"physicalInterface":{"lockout":0}}` |
| Wake and sleep brightness, percent | `{"source":3,"physicalInterface":{"activeIntensity":90,"idleIntensity":50}}` |
| Maximum / minimum setpoint | `{"source":3,"targetHeat":{"lockoutMax":29.5}}` / `{"source":3,"targetHeat":{"lockoutMin":5.5}}` |
| Temperature offset | `{"source":3,"tracking":{"ambientOffset":-0.5}}` |

The app sends both brightness values together. Setpoint limits and offset use 0.5 °C steps; the Lite offset range is −5 to +5 °C.

The coordinator gives the UI an optimistic value after a successful write by updating its cached state. It schedules a real read two seconds later to confirm or correct that value. The optimistic cache is not a device acknowledgement.

### Settings writes

`POST /devices/{dev}/` updates the older settings record and echoes the body. Keep the trailing slash used by the app and Python client.

| Change | Body | Observed result |
|---|---|---|
| Display units | `{"Format":"fahrenheit"}` or `{"Format":"celsius"}` | The thermostat and HomeKit units changed within seconds; `physicalInterface.format` stayed `"C"` |
| Heater type | `{"DutyCycleOpt":9}` for baseboard or `{"DutyCycleOpt":8}` for fan forced | The record changed; `bbConfig.reported.controlType` did not change within 30 seconds |
| Name | `{"Name":"Bedroom"}` | Account name |
| Time zone | `{"TimeZone":"America/Los_Angeles"}` | IANA time-zone name |

The newer state API and the older settings record are not interchangeable. Read the record for its fields and the reported state for device confirmation.

## Schedules, holds and shortcuts

### Weekly schedules

`POST /schedule` creates and attaches a schedule. The captured response was 201 with `{"scheduleId":"..."}`.

```json
{
  "name": "Schedule-{dev}",
  "deviceType": "BB",
  "homeId": "{home}",
  "reusable": false,
  "deviceIds": ["{dev}"],
  "events": [
    {
      "executionTime": "20:45:00",
      "dayOfWeek": "6",
      "clientKey": "App-4.20.1",
      "state": {
        "targetHeat": {"setpoint": 22},
        "modes": {"mode": 4}
      }
    }
  ]
}
```

`dayOfWeek` is a string from `"0"` (Sunday) through `"6"` (Saturday). Execution times are local to the device's time zone. Python expands each selected weekday into an event and uses `HomeAssistant-<integration version>` for `clientKey`. It formats the supplied time directly, without converting between HA's time zone and the device's `TimeZone`; those need to agree for the same wall-clock schedule.

Read schedules with `GET /schedule?homeId={home}&includeLegacyBaseboard=true&includeLegacyAc=true&includeLegacyInfloor=true`. It returns a list of objects with `id`, `name`, `reusable`, `homeId`, `deviceType`, `deviceIds`, `events`, `createdAt` and `updatedAt`. No schedule returned 404; Python converts that to an empty list.

`DELETE /schedule/{scheduleId}/attach/{dev}` detached the tested schedule and returned "Schedule unattached from device and deleted".

The app route table names `UPDATE_SCHEDULE /schedule/`, but an in-place edit was not captured. The HA implementation replaces a schedule by detaching the device's existing schedules, then creating a new one. Those calls are not atomic: if creation fails, the old schedule has already been detached.

### Holds

With a schedule active, a plain setpoint write held the new value until the next event. Explicit holds use `POST /schedule/hold`:

```json
{
  "deviceId": "{dev}",
  "holdType": "permanent",
  "startTime": "<future ISO UTC timestamp>",
  "state": {
    "modes": {"mode": 4},
    "targetHeat": {"setpoint": 22.5}
  }
}
```

For a timed hold, use `"holdType":"temporary"` and add `"endTime":"<ISO UTC timestamp>"`. Both returned 201 with a `holdId`. Changing the hold kept the same ID.

The server rejects a start time that has already passed with "Start time must be greater than current time". Python starts the hold ten seconds in the future and formats timestamps with milliseconds and a `Z` suffix. HA also applies its state immediately through the normal local-first control path.

The integration expects a device to follow a schedule before accepting a hold. It translates a creation-time 404 into "hold needs schedule"; the tests model that response with a mock.

`GET /schedule/hold/{dev}` reads the active hold; 404 means none. `DELETE /schedule/hold/{dev}` resumes the schedule. In the test, it returned "Device restored to follow its schedule" and restored the scheduled setpoint. Python treats a missing hold on resume as already complete.

### Home, Away and Vacation shortcuts

`GET /homes/{home}/scenes` returns `{"Scenes":[...]}`. Default shortcut IDs are `{home}_home`, `{home}_away` and `{home}_vacation`.

Edit a shortcut with `PATCH /scene/{shortcutId}`:

```json
{
  "DeviceActions": {
    "{dev}": {
      "type": 3,
      "mode": 4,
      "setpoint": 22,
      "holdTillKey": 0,
      "secToHold": null,
      "version": "V2"
    }
  },
  "AllDevices": false
}
```

The captured PATCH returned 202. Treat `DeviceActions` as the replacement action map. The app UI could not remove an action once set, but `{"DeviceActions":{},"AllDevices":true}` reset it through a direct PATCH.

Apply with `POST /homes/shortcut/apply/{shortcutId}` and `{"Timestamp":<Unix seconds>}`; the response was `Ok`. The Python client implements reading and editing shortcuts for account cleanup. It has no apply wrapper.

## Telemetry and history

### Usage

`POST /telemetry/usage/{dev}` accepts a time range and requested metrics. The app's usage request shape is:

```json
{
  "timezone": "America/Los_Angeles",
  "start": 1791615600,
  "end": 1791702000,
  "summary": {"oH1":"sum","k":"sum","t":"average","h":"average","n":"sum"},
  "aggregatedReadings": ["oH1","k","t","h","n"],
  "rawReadings": ["t","c","h","i","s","hs"]
}
```

The response is a list of local-day objects. Each has a day timestamp `T`, a `summary` and `aggregatedReadings`; requested raw data appears under `rawReadings`.

| Location | Meaning |
|---|---|
| `summary.a.oH1` | Heating on-time in seconds |
| `summary.a.t` / `h` / `n` | Average temperature / average humidity / sample count |
| `aggregatedReadings[].T` | Start of a 30-minute bucket |
| `aggregatedReadings[].a` | Bucket metrics, using the summary field names |
| `rawReadings[].t` | Sample time; raw readings were about 30 seconds apart |
| Raw `c`, `h`, `s` / `hs` | Room temperature, humidity and setpoint |

The app requests raw readings only for a one-day range. The Python `get_usage()` wrapper currently asks only for `oH1` and `t` summaries and buckets; it does not request raw history. It exposes daily runtime and average temperature through `UsageDay`.

### Humidity and Lite energy data

App 4.20.1 hides the Lite's Usage screen and returns early from `handleUsageFetch`. Direct usage requests still worked. The older app had a Lite energy-chart path behind `shouldBB2LAccessEnergyCharting`; that is evidence of an app gate, not a current-energy measurement.

The Lite supplied humidity despite `humiditySensor: false`. In the recorded comparison, Mysa readings around 35–37% gave a dew point of 6.4–6.5 °C, versus 5.6 °C on an Apollo AIR-1 in the same room. Its raw board temperature was about 27 °C; at that temperature the same moisture content would imply about 25% RH. This supports the interpretation that the reported humidity compensates for board heating. It is one room comparison, not a calibration. Mysa Local exposes the cloud reading; the Lite's HomeKit service has no humidity characteristic.

The usage response omitted `k` on the Lite. It has no current sensor, but `oH1` supplies heater runtime. An estimate using configured heater wattage is:

```text
energy_kWh = heating_seconds × heater_watts / 3_600_000
```

HA can fetch the current local day's cloud runtime every 15 minutes when enabled for that thermostat. Its local runtime tracker also integrates heating state and can be raised to the cloud total. These are runtime-based estimates, not measured Lite power consumption.

### State change history

`GET /state/history/{dev}?startTime=<Unix seconds>&endTime=<Unix seconds>` returned an object keyed by field, including `heatSetpoint`, `coolSetpoint` and `mode`. Each field has a list of `{timestamp,value,source}` entries.

Observed sources were `device`, `app`, `schedule` and `shortcuts`. The endpoint can therefore distinguish wall-button changes from scheduled or app changes. The app defines the route but no caller was found; it was tested directly. Python flattens the entries into `StateChange` objects and sorts them oldest first.

An older endpoint, `POST /energy/setpoints/device/{dev}`, accepts `{"Scope":"Day","Timestamp":<Unix seconds>,"PhoneTimezone":"America/Los_Angeles"}` and returns setpoint changes with `Prev`, `Next`, `Source` and `SourceTime`.

## Model-specific controls

### Mode and heater enums

The following values are `CONTROL_MODE_TYPE`, used in `modes.mode`. Capability documents use their names:

| Value | Name | Setpoint section in the app |
|---|---|---|
| 0 | off | None |
| 1 | auto | `targetAuto` |
| 3 | cool | `targetCool` |
| 4 | heat | `targetHeat` |
| 5 | emergencyHeat | `targetHeat` |
| 6 | preCool | Not mapped in the extracted setpoint table |
| 7 | fanOnly | None |
| 8 | dry | None |
| 9 | sleep | None |

Python defines these mode values, but its setpoint mapping implements heat, emergency heat and cool only. It has no `targetAuto` range implementation; other modes fall back to the heat setpoint in the current callers.

`DutyCycleOpt` is a different enum: 0 is BB1 baseboard, 4 BB1 radiant, 5/6/7 BB1 fan forced short/medium/long, 8 BB2 fan forced and 9 BB2 baseboard. The Lite uses the BB2 values. The 4.20.1 heater-type UI appears for V2 and Lite on firmware 3.17.0 or later, according to the decompiled gate.

### AC fan speed

This mapping comes from app 4.20.1 and is implemented in Python. It has not been tested on an AC.

The device record's `SupportedModes` lists fan speeds using the app's `Fan` enum. The app converts those values through `AC_NATIVE_FAN_TO_SHADOW_FAN_MODE` before writing `modes.fan_mode`:

| Meaning | Record `Fan` value | Shadow `fan_mode` |
|---|---|---|
| Auto | 1 | 0 |
| Low | 2 | 1 |
| Medium | 4 | 2 |
| High | 6 | 3 |
| Speed 1 | 3 | 4 |
| Speed 2 | 5 | 5 |
| Speed 3 | 7 | 6 |
| Speeds 4–7 | 8–11 | 7–10 |
| Sleep | 12 | 11 |

AC fan writes include the current mode:

```json
{"source":3,"modes":{"fan_mode":2,"mode":3}}
```

Here, shadow fan 2 is medium and shadow mode 3 is cool. Other capability-based models use `CONTROL_FAN_SPEED`: 0 off, 1–7 speeds 1–7, 8 sleep and 9 auto. Their app branch sends `fan_mode` alone. Do not use that generic enum for AC.

[Mysa_HA](https://github.com/kgelinas/Mysa_HA) v0.9.2's legacy MQTT `fn` values provide a cross-check: auto 1, low 2, medium 3, high 4 and sleep 12 are the corresponding AC shadow values plus one. That comparison is not hardware verification.

### AC swing and supported remote commands

`CONTROL_SWING` is 0 invalid, 1 off, 2 on, 3 auto, 4–10 positions 1–7, 11 toggle and 12 not available. State writes need no fan conversion or current mode:

```json
{"source":3,"modes":{"verticalSwingState":2}}
```

Horizontal swing uses `horizontalSwingState`. Toggle-only remotes use `verticalSwingToggle` / `horizontalSwingToggle` with `SWING_TOGGLE_MODES` 1 off and 2 on. Mysa Local implements explicit states, not toggle-only commands.

The remote's supported controls come from its `GET /devices` record:

- `SupportedModes` is keyed by legacy mode strings: `"1"` off, `"2"` auto, `"3"` heat, `"4"` cool, `"5"` fan and `"6"` dry. Each entry has `temperatures`, `fanSpeeds`, `verticalSwing` and `horizontalSwing`.
- `SupportedKeys` and `modifiedKeys` contain IR key IDs. With a nonempty `modifiedKeys`, the app keeps only fan speeds whose key is present: auto 8; low/speed 1 → 9; medium/speed 2 → 10; high/speed 3 → 11; speeds 4–7 → 16–19; sleep → 20.
- The same customization removes a swing direction if none of its keys remain. Vertical keys are 12/39/40/47; horizontal keys are 15/41/42.
- `SupportedCapsVersion: "1.2"` identifies the newer remote format with numbered speeds.

The app hides fan selection while off. Python applies the current-mode and customized-key filters in `DeviceRecord` and converts fan values on reads and writes. Fan and swing writes use the cloud.

### Remote temperature tracking

App 4.20.1 links Climate Sensors to thermostats through `GET/POST /linking` with `{sourceId,targetId,weight}`. Its Linked Sensors menu is limited to Central HVAC. A remote-tracking write is:

```json
{"source":3,"tracking":{"tracking":2,"remoteTTL":480}}
```

Tracking modes are 0 ambient, 1 probe and 2 remote; 480 seconds is the app's default remote TTL. The app does not submit a replacement temperature. The cloud supplies the linked sensor's readings.

The Lite advertises `sensing.temperature.sensors.remote: false`, `trackingSensor.validValues: ["internal"]` and no external temperature probe connector. `GET /linking?targetId={dev}` returned an empty list. A direct remote-tracking write reached `desired`, but no `tracking` value appeared in `reported`, in either normal or live mode.

As a control experiment, `ambientOffset: 0.5` was acknowledged in about one second and raised both cloud and HomeKit room temperature by 0.5 °C. Resetting with `{"source":3,"tracking":{"tracking":0,"ambientOffset":0}}` restored the offset; the unsupported tracking fields remained inert in `desired`. The supported offset changes both the temperature used for control and the reported room temperature.

## Adding and removing devices

Wi-Fi provisioning, account ownership and local HomeKit pairing are separate operations. An older thermostat can join Wi-Fi and work through HomeKit without being claimed by a Mysa account.

### Claiming an older model

The older families are Baseboard V1/V2/Lite, In-floor and AC. In app 4.20.1, setup proceeds through Wi-Fi configuration, `CONNECTED`, `ADDING_TO_ACCOUNT`, `VERIFYING_CONNECTION`, firmware and completion. The app claims the device before the cloud has necessarily seen it online. The Lite verification code uses a legacy-echo check with a four-minute timeout; this is an app-code finding, not evidence that 4.20.1 opens its own MQTT session.

`POST /devices` claims the device. App 4.20.1 sent this Lite body to `mysa-backend`:

```json
{
  "Id": "{dev}",
  "Name": "Bedroom",
  "Model": "BB-V2-0-L",
  "Home": "{home}",
  "TimeZone": "America/Los_Angeles",
  "LastPaired": 1791700000,
  "Animation": "off",
  "ecoMode": 1,
  "Format": "celsius",
  "schedGlobalOffset": 0,
  "DutyCycleOpt": 9
}
```

The name defaults to the ID, the time zone comes from the phone and units follow the user's preference. This request body contains no possession proof beyond the device ID. We tested re-adding a previously owned Lite; claiming a device never on the account was not tested. The app's Lite body defaults to baseboard heating. The old app's `ECO_MODE` enum defines 1 as off; 4.20.1 removed the Eco Mode UI and writes 1 during re-pairing.

AC adds `SetupRequired: true`. In-floor adds `DutyCycleOpt: 0`, `FloorOhms: 10`, `FloorType: 1`, `IsMysaFloorSensor: true`, `MaxFloorTemp: 28`, `MaxSetpoint: 28` and `Tracking: 3`. BB1 uses `DutyCycleOpt: 0`. These other-model defaults come from app code.

App 3.54.1 sent a smaller body to `app-prod`: `Id`, `TimeZone`, `Name`, `LastPaired`, `Home` and `Model`, plus AC's `SetupRequired` or the Lite's `schedGlobalOffset: 0`. For property-manager accounts, `Home` was `"PROPERTY_HOME"`.

The observed and app-defined outcomes differ:

| Outcome | Meaning and handling |
|---|---|
| 201 on the tested re-add | Device added; the app reloads account data and creates default home shortcuts if needed |
| 409 | Already on this account; response is the existing record and the submitted fields are not written |
| Error text contains "User is not allowed to access the specified home" | The app shows Contact Support for the ownership conflict; another-account behavior and its HTTP status were not tested |
| "No Internet Connection" | The app retries up to three times |
| Other failure | The app shows "Failed To Add Mysa to Account" |

After 409, app 3.54.1 offered Keep or Overwrite. Overwrite sent the body to `POST app-prod /devices/purgeadd`. That route was also checked directly and still worked. App 4.20.1 has no overwrite dialog or `purgeadd` call; it keeps the record, writes `ecoMode: 1` through the settings endpoint and records a re-pair.

Python `add_device()` returns `True` after success or `False` on 409. `replace_device()` implements `purgeadd`; omitted settings may be reset by replacement. Both methods reject unknown models and families requiring Bluetooth credentials before making a request.

### Deletion and re-addition

`DELETE /devices/{dev}` has no body and returned 204. Both hosts worked in the test; Python uses `mysa-backend`.

The app's `useDeviceDeletion` flow does more than this one request:

1. Refuse deletion in the UI unless the user is the home's admin.
2. Remove the thermostat's actions from shortcuts.
3. Detach its schedule through `/schedule/{id}/attach/{dev}`, or remove a legacy schedule through `/schedules/v2`.
4. Delete sensor/HVAC links and clear `GroupName` on grouped devices.
5. Delete the account record and remove the device from local app data.

`MysaClient.delete_device()` performs only the final API deletion. HA's `async_remove_from_account()` first attempts to detach schedules and remove shortcut actions. That cleanup is best effort: an API error is logged and the record deletion still runs. It does not implement the app's sensor/HVAC-link or group cleanup.

After deletion, the Lite disappeared from `GET /devices` and bootstrap's `AllowedDevices`. Its individual state endpoint returned 404 with "User does not have access to this device"; the batch endpoint returned `PERMISSION_DENIED` for that ID. The thermostat stayed connected to Mysa and HomeKit control kept working.

The live delete/re-add experiments returned 201 and restored the live setpoint, mode, limits, brightness, lock, offset and control type. Capabilities and home shortcuts were still available. These experiments removed the account record directly; they did not run the app's preceding cleanup.

Fields supplied in the new claim overwrite stored record values. In one test, re-adding with `DutyCycleOpt: 9` changed the record from fan forced to baseboard while the thermostat still reported `controlType: 8`. Fields omitted from the claim, including brightness limits, button timings and voltage, returned unchanged. That does not establish whether the server retained every omitted field or restored defaults that happened to match.

For a returning device, Python supports `app_defaults=False` to send the smaller 3.54.1 body without explicit settings defaults. The HA account flow uses this option and HA's time zone; the client method itself defaults to `True`. Supply the intended name, home and time zone because those fields are still written. Explicit `heater_type` and `extra` fields are sent in either mode.

### Baseboard V3, Central HVAC and Climate Sensor

This flow comes from app 4.20.1 and has not been tested here. These devices need cloud credentials in addition to Wi-Fi details:

1. Read the serial number over Bluetooth Device Information characteristic `2a25`.
2. Request `POST /pairing/credentials` with `{deviceId,model,serialNumber}`. Decode the base64 `credentials` field and transfer it over Bluetooth during setup.
3. On the device's "register device" status, send `POST /ota/fleet/devices` with `{"deviceIds":["{dev}"],"fleetName":"<fleet>"}`. Fleets are `bb3-prod`, `lv-prod` and `cs-prod`.
4. Claim with `POST /devices`, using the base fields without legacy heater defaults.
5. Wait up to five minutes for a newer `identity` timestamp from `GET /state/{dev}`.
6. Send `POST /pairing/finalize` with `{"deviceId":"{dev}"}`, then use the newer OTA flow.

Credential transfer uses service `ecaa050b-0da4-e394-d64c-5d08e47cb41d`, control characteristic `708aa387-184f-fca4-6b4e-eef896ec9bb5` and data characteristic `81838135-4ca1-96b1-1b47-6e96772fef72`. Control commands are 0 none, 1 start and 2 cancel; statuses include 0 idle and 1 downloading. The exact start packet, chunk sizing and completion signal have not been extracted.

After setup, `GET /matter/qr/{dev}` returns `{qrCode,pairingCode}`. The app describes Matter over Wi-Fi and adding a device already on the network. The code comes from the cloud instead of a printed HomeKit label. HA would pair these through its Matter integration. Mysa Local does not implement their provisioning.

### Local provisioning protocols

BLE and SoftAP HTTP configure Wi-Fi; neither app bundle exposes thermostat runtime controls through them.

The BLE Wi-Fi service is `4d790000-7361-5769-4669-436f6e666967`, with config/status/identify characteristics starting `4d790001`, `4d790002` and `4d790003`. Config commands are one-byte `0x20` start and `0x2F` end. SSID (`0x21`) and password (`0x22`) packets are `[type, length, ASCII(base64(UTF-8(value)))]`; length counts the base64 text. Wait for the matching status notification. `0x41` means connected. The separate [mysa-wifi-setup repository](https://github.com/SokolskyNikita/mysa-wifi-setup) implements this sequence, which was tested on the Lite.

The setup HTTP server is `http://192.168.4.1:57961`. The app reads `/pubkey`, `/hwid` and `/model`, then sends `GET /network/` with an RSA-encrypted JSON `{ssid,pass}` value in the `mysa-data` header. It polls `/connected/` (200 joined, 202 pending, 404 failed) and calls `/reboot/`. App 3.54.1 also called `/identify`. Its ciphertext header was JSON-quoted; 4.20.1 strips line breaks and sends it unquoted.

On two Lite units in normal Wi-Fi mode, port 57961 refused connections. The only open TCP port found was HomeKit on 57959. An unauthenticated HomeKit HTTP request returned 470. These observations do not establish a usable local REST control API.

## Firmware

### Legacy devices

For V1/V2/Lite, In-floor and AC, `GET /devices/update_available/{dev}` returns:

```json
{"update":false,"installedVersion":"3.17.5.13","allowedVersion":"3.17.5.13"}
```

The 4.20.1 trigger is `POST /devices/{dev}/firmware-update` with no body. The phone requests an update; it does not download or transfer a firmware image. The app's only BLE file transfer is the newer models' pairing credentials.

The app checks availability every 20 seconds during pairing and re-sends the trigger while `update` remains true. It gives up after six minutes. A home-screen variant checks every ten seconds and then reads `GET /devices/firmware`, which lists installed versions. The Lite's home-screen update sheet is suppressed; its update flow runs after pairing.

The trigger was tested on October 11 at 05:33 Pacific with no update available. It returned `{"triggered":true,...}`. About one second later, the router capture showed a 130-byte TLS record arriving on the thermostat's existing AWS IoT connection, followed by DNS resolution and a short HTTPS connection to `devices10010.getmysa.com`. There was no firmware download, reboot or version change. The encrypted traffic did not reveal the request path or payload.

That sequence is consistent with the thermostat receiving the trigger and checking for firmware. It does not verify installation of an available release. Python implements the trigger; HA exposes installation only when its firmware-install option is enabled. The HA update entity sends the trigger once, then polls every 20 seconds until `update` is false, with a six-minute timeout.

App 3.54.1 used the same availability check but triggered the device through MQTT `MsgType: 6, EventType: 2` instead of REST. It repeated the notification with its ten-second availability checks and recognized boot messages `MsgType: 10` or MCM `msg: 61` as another completion signal.

### Newer OTA service

The 4.20.1 code for V3, Central HVAC and Climate Sensor uses `GET /ota/update/available/{dev}`, `POST /ota/update` with `{deviceIds,versionId,requestSource:"USER",...}`, `GET /ota/update/{requestId}` and `PUT /ota/update/{requestId}/cancel`. Fleet registration happens during pairing.

The Lite returned 404, "No fleet found containing device", from this availability endpoint. Python does not implement the new OTA service.

## Legacy MQTT

### What was tested

A direct MQTT attempt used Cognito identity credentials and a SigV4-signed WebSocket URL at `wss://a3q27gia9qg3zy-ats.iot.us-east-1.amazonaws.com/mqtt`. TLS and the WebSocket upgrade succeeded with HTTP 101 and subprotocol `mqtt`. The broker then closed the connection after MQTT CONNECT, without CONNACK.

That result applies to the tested identity and connection flow. It does not prove that all app accounts have lost MQTT access.

Later analysis of 3.54.1 found a missing step: the old app calls `AttachPrincipalPolicy` with policy `default` and the Cognito identity ID before connecting. Its old client does so on every connection; its newer client relies on an attachment at sign-in. Neither the tested Mysa_HA flow nor app 4.20.1 performs that call.

A missing policy is therefore a plausible explanation for the rejection. We have not tested whether the current Cognito role permits attachment or whether it would fix the connection. Mysa Local has no MQTT client and uses the verified HTTPS path.

### App 3.54.1 protocol

These details come from decompiling app 3.54.1.

| Item | App behavior |
|---|---|
| Connection | MQTT 3.1.1 over SigV4 WebSocket, service `iotdevicegateway`, region `us-east-1`, fresh UUID client ID, 60-second keepalive |
| Commands | Publish to `/v1/dev/{dev}/in`, normally QoS 1 |
| Readings | Subscribe to `/v1/dev/{dev}/out` at QoS 1; the app does not subscribe to `/batch` |
| Publish queue | One message per 350 ms tick |
| Live reporting | `{"Device":"{dev}","MsgType":11,"Timeout":180}` every 150 seconds while foregrounded |
| Echo | `{"Device":"{dev}","EventType":4294967295,"MsgType":6,"EchoID":418,"Timestamp":<Unix seconds>}`; expects `MsgType:5` with the same echo ID |
| Settings notification | REST settings write, then `MsgType:6, EventType:0` |
| Schedule notification | REST schedule write, then `MsgType:6, EventType:1` |

The Lite always takes the MCM `msg:44` command path in this app. V1, V2 and In-floor require firmware 3.11.0 for that path; AC requires 3.12.0. A Lite setpoint command has this shape:

```json
{
  "ver": "1.0",
  "src": {"type":100,"ref":"<user ID / Cognito sub>"},
  "dest": {"type":1,"ref":"{dev}"},
  "time": 1791700000,
  "msg": 44,
  "id": 1791700000000,
  "body": {"ver":1,"type":5,"cmd":[{"tm":-1,"sp":21.5}]}
}
```

`time` is seconds and `id` is milliseconds. Device types are 1 baseboard V1, 2 AC, 3 In-floor, 4 V2 and 5 Lite. The app publishes each command twice: first with `cmd` as an array, then with that array encoded as a JSON string. Its newer MQTT client adds a seconds-based `Timestamp`. The generator does not set `resp`.

Command fields include `sp` setpoint, `md` legacy mode (1 off, 3 heat), `ho` schedule behavior (1 follow, 2 hold) and `tm` execution time (`-1` now). A timed hold sends a hold-now entry followed by a follow-schedule entry at the expiry time. Brightness objects use `br`. The app does not send `lk`, `stpt` or `a_sp` as Lite command fields.

An acknowledgement uses `resp_id` to match the command ID and exposes state under `body.state`, including `md`, `sp`, `ho`, `tr`, `fn` and `ss`. The app's parser does not inspect `success`, `trig_src`, `br` or `lk`. Live BB2/Lite readings use MCM `msg:40`, with `body.hum`, `ambTemp`, `stpt` and `dtyCycle`.

Legacy `MsgType` and MCM `msg` are different namespaces. The app enum calls legacy `MsgType:7` REBOOT. Legacy `MsgType:40` is also different from MCM `msg:40` telemetry and from the app's `MsgType:6` echo above. Do not use `MsgType:7` or `MsgType:40` as a liveness probe. The old source enum defines 0 as device manual and 1 as device schedule.

## Other app endpoints

These routes are useful for further investigation but have no dedicated Python client methods unless described earlier.

| Request | Finding |
|---|---|
| `GET /homes` | Tested home had `Zones: []` |
| `GET /devices/{dev}/telemetry_alerts` | Lite returned `{"Items":[]}` |
| `GET /smart-early-on/{dev}/availability` | Lite returned `available:false` with `model-not-eligible:BB-V2-0-L`; the server enforces this restriction |
| `POST /energy/v3/{home}/home` | Whole-home energy returned 404 on the Lite account |
| `GET /homes/{home}/savedSchedule` | Returned `{"SavedSchedules":[]}` |
| `GET /schedule/compatibility/{dev}` | Returned `[]` |
| `GET app-prod /devices/state` | Legacy state: timestamped `CorrectedTemp`, `SensorTemp`, `HeatSink`, `Humidity`, `SetPoint`, `Duty`, `OnTime`, `OffTime`, `Lock`, `Brightness`, `Mode`, `TstatMode`, `ScheduleMode`, `HoldTime`, `Rssi`, `Connected`, `Current`, `Voltage`, `FreeHeap` |
| `GET /feature-flags/features/check?versionNumber=4.20.1` | Returns `{"features":{"<NAME>":{"value":true}}}`; all returned flags in the test were true except `HIDE_MATTER_IN_APP` |
| `POST /network-diagnostics/test` | Uploads the phone's `WifiConnection`, `SpeedTest` and `MysaConnectivity` results |
| `GET /network-diagnostics/history` | Reads those uploads; neither diagnostics route probes the thermostat |
| `PATCH /users/{user}` | Sign-in metadata: `LastAppVersion`, `OperatingSystem`, `DeviceType`, `LanguagePreference` |
| `POST app-prod /users/pushNotification` | Registers a push token |

Other GET routes seen include `/homes/{home}/geofences`, `/homes/reminders/{home}`, `/homes/{home}/users`, `/invite/users/{user}`, `/devices/{dev}/drstate`, `/communication-preferences/` and notification preferences under `/preferences/User_{user}_Home_{home}/Notifications`.

Model and firmware flags include `LV_ACCESS`, `LV_AUTOMODE_FW`, `LV_ADAPTIVE_BRIGHTNESS_FW`, `LV_STAGED_AUX_HEAT_FW`, `LV_FREEZE_PROTECTION_ENABLE_FW`, `LV_TEMP_DIFFERENTIAL_FW`, `LV_EXTERNAL_PROBE_FW`, `BB3_SMART_EARLY_ON_ACCESS`, `ENROLL_BB3_DR`, `SMART_EARLY_ON_V2_ACCESS`, `ADAPTIVE_BRIGHTNESS_ACCESS`, `STAGED_AUX_HEAT_ACCESS` and `DR_ENROLMENT_WEB_FLOW_ACCESS`. Their presence in a response does not establish that a particular thermostat supports the feature.

The older app also defines `/energy/device|zone|home/{id}`, `/batchReadings/v2/{dev}`, `/schedules`, `/schedules/device/update/{dev}` and `/devices/capabilities`. Their continued availability was not tested. Its `dash-api-v1.mysa.cloud` client is for property-manager `GET /zones`; `/users/readingsForUser` is a defined route with no caller found. Old NFC setup uses `GET /nfc/auth-key`; that path is absent from 4.20.1.

## Python implementation and evidence

### Code map

| File | Responsibility |
|---|---|
| [api/auth.py](../custom_components/mysa_local/api/auth.py), [api/srp.py](../custom_components/mysa_local/api/srp.py) | Cognito sign-in, refresh and SRP |
| [api/client.py](../custom_components/mysa_local/api/client.py) | HTTP requests and endpoint wrappers |
| [api/models.py](../custom_components/mysa_local/api/models.py), [api/const.py](../custom_components/mysa_local/api/const.py) | Response parsing, capability ranges, model names and protocol enums |
| [cloud.py](../custom_components/mysa_local/cloud.py) | Polling, write spacing, optimistic values and refresh scheduling |
| [thermostat.py](../custom_components/mysa_local/thermostat.py) | Local/cloud selection, timeout readback, AC controls and runtime tracking |
| [schedule.py](../custom_components/mysa_local/schedule.py) | HA hold and weekly-schedule actions |
| [account.py](../custom_components/mysa_local/account.py) | Cloud account operations for local thermostats |
| [update.py](../custom_components/mysa_local/update.py) | Firmware check and optional installation |

Cloud state polling defaults to 60 seconds and is configurable from 30 to 300 seconds. Records refresh every ten minutes, holds every five minutes, usage every 15 minutes when enabled, firmware every six hours and capabilities every 24 hours. Optional-read failures keep the previous values. Main polling failures double the interval up to ten minutes; a successful poll restores the normal interval.

The live-mode option is off by default. When enabled and a paired thermostat loses its LAN path, HA requests 300 seconds of live reporting for that home, renews it every 240 seconds and polls every ten seconds. When all local paths recover, renewal stops and normal polling resumes. The already-requested stream expires on its own.

Cloud writes are serialized with at least 0.5 seconds between calls and followed by a refresh after two seconds. Setpoint, mode and display-unit writes use HomeKit first when available. After a local write timeout, HA reads the local value back and, for setpoint or mode, can check cloud-reported state before retrying through HTTPS. A device rejection is reported as a rejection. Cloud-only controls go directly to their cloud endpoint.

### Tests and fixtures

[tests/api/test_client.py](../tests/api/test_client.py) covers request bodies, response parsing, authentication failures and account operations. [tests/test_features.py](../tests/test_features.py), [tests/test_routing.py](../tests/test_routing.py) and [tests/test_ac.py](../tests/test_ac.py) cover the HA behavior and AC mappings. These tests use fixtures or mocks; they make no claim of live coverage for another model.

Sanitized examples are in [tests/fixtures](../tests/fixtures): `bootstrap.json`, `devices.json`, `state_batch.json`, `capabilities_bb_v2_0_l.json`, `usage.json`, `state_history.json` and `homekit_bb_v2_0_l.json`. They retain response shapes without the account's identifiers or credentials.

### Investigation method

The Android apps package their JavaScript as `assets/index.android.bundle`. Version 4.20.1 uses Hermes bytecode v96; 3.54.1 uses v90. Both were decompiled with `hermes-dec` / `hbc-decompiler`. Raw route extraction can merge adjacent strings, so an extracted route name alone is not a verified URL or request.

Live app traffic was captured through mitmproxy using a patched 4.20.1 build. A full apktool rebuild broke a nine-patch image and crashed the sign-in screen. The working patch replaced only the network security XML and an unused raw resource containing the proxy CA, then re-signed the APK splits. The original config permits cleartext only for `10.0.2.2`, `localhost` and `192.168.4.1`.
