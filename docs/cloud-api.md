# Mysa cloud API: supplementary reference

This document covers endpoint details, model differences, and service behavior
that supplement the protocol guides. Start with the relevant guide:

| Reference | Covers |
| --- | --- |
| [Cloud protocol](../custom_components/mysa_local/protocols/cloud/README.md) | Client usage, authentication lifecycle, common requests, state semantics, errors and retries |
| [Bluetooth protocol](../custom_components/mysa_local/protocols/BT/README.md) | Device information, Wi-Fi provisioning, network scanning, credential-file transfer and post-setup verification |
| [HomeKit protocol](../custom_components/mysa_local/protocols/homekit/README.md) | Local discovery, pairing, authenticated sessions and thermostat control |
| [MQTT protocol](../custom_components/mysa_local/protocols/MQTT/README.md) | Legacy cloud messaging, AWS authorization and known access limitations |

Hardware findings apply to Baseboard Lite. Other-model behavior is derived from
client code and is not hardware validation. Exact source versions and validation
scope are listed in [Sources and validation](#sources-and-validation).
[Sanitized response fixtures](../tests/fixtures) provide complete examples.

Unless specified otherwise, paths use `https://mysa-backend.mysa.cloud`.
`{dev}` is the Wi-Fi MAC in lowercase without separators, `{home}` is a home ID,
and `{user}` is a user ID. Temperatures are Celsius and numeric timestamps are
Unix seconds unless stated otherwise.

- [Protocol families](#protocol-families)
- [Connection and authentication](#connection-and-authentication)
- [Account discovery and capabilities](#account-discovery-and-capabilities)
- [Thermostat state](#reading-and-writing-thermostat-state)
- [Schedules, holds and shortcuts](#schedules-holds-and-shortcuts)
- [Telemetry and history](#telemetry-and-history)
- [Model-specific controls](#model-specific-controls)
- [Adding and removing devices](#adding-and-removing-devices)
- [Firmware](#firmware)
- [Additional endpoints](#additional-endpoints)

## Protocol families

Mysa's cloud interfaces coexist rather than forming one versioned API. This
reference uses the following names to distinguish their roles:

| Family | How it works | Scope |
| --- | --- | --- |
| Current HTTPS API | Structured state, capabilities, telemetry and schedules; the backend delivers commands to the thermostat | Used by Mysa Local, including for supported legacy thermostats |
| Legacy REST and MQTT | REST manages account/settings records and history; direct MQTT carries commands, notifications and telemetry | Several REST routes remain in use alongside current HTTPS; direct MQTT has separate authorization constraints |
| Newer provisioning and OTA | Cloud-issued credentials, fleet registration, pairing finalization and tracked firmware-update requests | Baseboard V3, Central HVAC and Climate Sensor; not implemented by Mysa Local |

These are descriptive names, not vendor-assigned protocol versions. A legacy
thermostat can use current state endpoints while retaining legacy account and
firmware flows. The [MQTT connection model](../custom_components/mysa_local/protocols/MQTT/README.md#connection-and-authorization-model)
covers the distinction between client-to-cloud and thermostat-to-cloud traffic.

## Connection and authentication

### Service hosts

| Host | Use |
| --- | --- |
| `mysa-backend.mysa.cloud` | Current HTTPS API and migrated legacy REST routes |
| `app-prod.mysa.cloud` | Legacy REST host; still advertised as `regionApiBaseUrl`, with some push-registration and account routes |
| `cognito-idp.us-east-1.amazonaws.com` | Sign-in and token refresh |
| `i18n.mysa.cloud` | Static country and time-zone lists |
| `chunk.mysa.cloud/track` | Analytics |

Routing depends on feature flags: `MONOLITH_API_ACCESS` moves legacy routes to
`mysa-backend`, and `DISABLE_LEGACY_FALLBACK` disables retrying them on `app-prod`.
Both were enabled for the tested account. A host name alone therefore does not
identify the protocol family. Mysa Local's host selection is described in the
[cloud guide](../custom_components/mysa_local/protocols/cloud/README.md#how-it-is-organized).

### Cognito sign-in

Current and legacy clients share these public Cognito identifiers:

| Setting | Value |
| --- | --- |
| AWS region | `us-east-1` |
| User pool | `us-east-1_GUFWfhI7g` |
| App client | `6cktj934gasnc72f7jo2cmf6rt` |
| Identity pool | `us-east-1:e27f246e-ad23-41ce-ac80-eda86f8be424` |

The app client has no client secret. Cognito requests go to `https://cognito-idp.us-east-1.amazonaws.com/` with:

```http
X-Amz-Target: AWSCognitoIdentityProviderService.<Action>
Content-Type: application/x-amz-json-1.1
```

No AWS request signature is required for these user-pool calls. Sign-in uses `InitiateAuth` with `AuthFlow: "USER_SRP_AUTH"` and `AuthParameters` containing `USERNAME` and `SRP_A`. A `PASSWORD_VERIFIER` challenge follows. `RespondToAuthChallenge` sends `PASSWORD_CLAIM_SIGNATURE`, `PASSWORD_CLAIM_SECRET_BLOCK`, `TIMESTAMP` and `USERNAME` as `ChallengeResponses`, plus the challenge session if supplied. The response contains `IdToken`, `AccessToken`, `RefreshToken` and `ExpiresIn: 3600`.

The Python SRP implementation uses Cognito's 3072-bit group, SHA-256 and a 16-byte derived authentication key. Use the challenge's `USER_ID_FOR_SRP` when computing the response, even when sign-in began with an email address. Cognito also expects an English UTC timestamp with an unpadded day, for example `Fri Jan 2 03:04:05 UTC 2026`. The [SRP implementation](../custom_components/mysa_local/protocols/cloud/srp.py) contains the calculation.

API authorization headers, token handling and supported challenges are described
in the cloud guide: [authentication](../custom_components/mysa_local/protocols/cloud/README.md#using-the-client) and [request handling](../custom_components/mysa_local/protocols/cloud/README.md#errors-retries-and-integration-boundary).

Refresh uses `GetTokensFromRefreshToken` with:

```json
{"ClientId":"6cktj934gasnc72f7jo2cmf6rt","RefreshToken":"<refresh token>"}
```

The observed refresh response supplied ID and access tokens with a one-hour
lifetime and no replacement refresh token. The Identity Pool is not needed for
HTTPS; the current client uses it for SNS push registration. Its credential exchange and
MQTT role are covered in the [MQTT connection model](../custom_components/mysa_local/protocols/MQTT/README.md#connection-and-authorization-model).

For HTTP errors and retries, see the [cloud error reference](../custom_components/mysa_local/protocols/cloud/README.md#errors-retries-and-integration-boundary).
The current client treats 409 as success generally; the Python client handles it specially
only for [device claims](#claiming-an-older-model).

### Browser access

The tested APIs do not support ordinary cross-origin browser access:

| Service | Browser behavior |
| --- | --- |
| Cognito | Browser sign-in allowed |
| `mysa-backend` | Authorization/Content-Type preflights returned 404; actual responses also lacked `Access-Control-Allow-Origin` |
| `app-prod` | Legacy-route preflights allowed access, but actual responses lacked `Access-Control-Allow-Origin`; newer pairing, state and bootstrap routes returned 403 |

Successful Cognito sign-in therefore does not make Mysa's API responses readable
by a webpage. Cloud requests need a backend, such as Home Assistant, or an
extension with the appropriate host permissions.

## Account discovery and capabilities

`GET /mobile/bootstrap?versionNumber={clientVersion}` returns account data and routing metadata. `clientVersion` is the mobile-client version, not an API version; the validated value is recorded in [Sources and validation](#sources-and-validation). Omitting `versionNumber` returned 400 with `FST_ERR_VALIDATION`. The response has `meta` and `data` objects: home selection is in `meta.primaryHomeId` / `activeHomeId`; `data.user` contains `Id`, `AllowedDevices`, `AllowedHomes` and Cognito attributes. The bootstrap fixture also records `data.homes` and `data.devices`.

`GET /devices` returns `{"DevicesObj":{"{dev}":{...}}}`. This is the account's settings record. Its fields include `Id`, `Model`, `Name`, `Home`, `TimeZone`, `LastPaired`, `Format`, `DutyCycleOpt`, `HeaterType`, `ButtonState`, brightness and setpoint limits, `UserTempOffset`, `ecoMode`, `Animation`, button timing values, `Voltage` and `MaxCurrent`. The record and the thermostat's reported state can disagree.

`GET /capabilities/{dev}` describes the device's supported features and valid values. Compatibility queries append:

```text
?includeLegacyBaseboard=true&includeLegacyAc=true&includeLegacyInfloor=true
```

The Lite request also worked without those parameters. Its response reported:

| Area | Lite values |
| --- | --- |
| Climate control | Off and heat; setpoint 5–30 °C in 0.5 °C steps; control type 8 or 9 |
| Temperature sensing | Offset −5 to +5 °C in 0.5 °C steps; internal tracking only |
| Interface | Lockout 0 or 3; C/F display units; wake and sleep brightness |
| Hardware sensor flags | Humidity, proximity and ambient light all `false` |
| Connectivity | HomeKit, Google Home and Alexa `true`; Matter `false` |
| Smart features | Scheduling, geofence and shortcuts `true`; energy reporting, Early On, zoning and saved schedules `false` |

The humidity flag conflicts with the [observed readings](#humidity-and-lite-energy-data). It must not be used to discard humidity that the thermostat actually reports.

The Python `Capabilities` parser reads `features.climateControl`, `features.sensing`, `features.interface`, `features.connectivity`, `features.smart`, `hardware.sensors` and `system`. It ignores controls marked `userControllable: false`, derives ranges from `validValues` and retains the original response in `raw`.

Legacy Baseboard, AC and In-floor devices are exposed through the current `/state`, `/capabilities` and `/schedule` adapters. Their presence there does not imply support for newer credential provisioning or fleet-based OTA.

## Reading and writing thermostat state

### State reads

See the [cloud request table](../custom_components/mysa_local/protocols/cloud/README.md#requests-and-state)
for the single-device and batch endpoints. A single-device read returns the
state object directly; a batch returns `{"{dev}":{"data":<state>}}`, with errors
possible for individual IDs. `get_states()` omits failed entries.

Online status can remain true while readings are stale. Use
`latestTelemetry.reading.timestamp` for freshness; the
[live reporting behavior](#live-mode-and-liveness) explains the delay.

### State structure

The [cloud state semantics](../custom_components/mysa_local/protocols/cloud/README.md#requests-and-state)
explain `desired`, `reported`, and telemetry. Unsupported fields can remain in
`desired` indefinitely without appearing in `reported`.

The [state fixture](../tests/fixtures/state_batch.json) shows the full response shape. The main sections are:

| Section | Fields and interpretation |
| --- | --- |
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

Additional state-section names include `latestTelemetry`, `targetHeat`, `targetCool`, `targetAuto`, `modes`, `tracking`, `physicalInterface`, `hvacConfig`, `identity` and `telemetry`. The response can contain further sections, as the table shows.

### Live mode and liveness

Outside live mode, the tested Lites sampled about every 31 seconds but uploaded in batches 12–30 minutes apart. `latestTelemetry` and usage `rawReadings` advanced together; neither supplied fresher data between uploads. This request changed the home's reporting interval to about five seconds:

```http
POST /mysa-legacy/stream/home/{home}
Content-Type: application/json

{"timeout":300}
```

The response has `message`, `streamedDeviceIds`, `skippedDeviceIds` and `failedDeviceIds`. The reference client renews a 300-second stream about every 4.2 minutes. Live mode changes the thermostat's reporting rate; the client still has to poll state.

For a cloud liveness check, record the reading timestamp, request a short stream, for example `{"timeout":20}`, then poll `GET /state/{dev}` until that timestamp advances. The first fresh reading arrived about one second after the test request. Readings stopped when the short stream expired. A returned ID in `streamedDeviceIds` alone does not confirm that the thermostat responded.

Same-value writes are also unsuitable as a ping. Writing an unchanged offset or brightness pair returned 200 without advancing `reported.timestamp`.

### State writes

Use the [state-update request format](../custom_components/mysa_local/protocols/cloud/README.md#requests-and-state). Source 3 appears as `"app"` in state history. The success reply is `{"message":"Successfully updated state for device {dev}"}`.

These changes reached the Lite's `reported` state within about one second:

| Change | Complete request body |
| --- | --- |
| Heat / off | `{"source":3,"modes":{"mode":4}}` / `{"source":3,"modes":{"mode":0}}` |
| Lock / unlock buttons | `{"source":3,"physicalInterface":{"lockout":3}}` / `{"source":3,"physicalInterface":{"lockout":0}}` |
| Wake and sleep brightness, percent | `{"source":3,"physicalInterface":{"activeIntensity":90,"idleIntensity":50}}` |
| Maximum / minimum setpoint | `{"source":3,"targetHeat":{"lockoutMax":29.5}}` / `{"source":3,"targetHeat":{"lockoutMin":5.5}}` |
| Temperature offset | `{"source":3,"tracking":{"ambientOffset":-0.5}}` |

Send both brightness values together. Setpoint limits and offset use 0.5 °C steps; the Lite offset range is −5 to +5 °C.

### Settings writes

The [settings-update endpoint](../custom_components/mysa_local/protocols/cloud/README.md#requests-and-state) echoes the submitted body. Its fields can diverge from reported device state:

| Change | Body | Observed result |
| --- | --- | --- |
| Display units | `{"Format":"fahrenheit"}` or `{"Format":"celsius"}` | The thermostat and HomeKit units changed within seconds; `physicalInterface.format` stayed `"C"` |
| Heater type | `{"DutyCycleOpt":9}` for baseboard or `{"DutyCycleOpt":8}` for fan forced | The record changed; `bbConfig.reported.controlType` did not change within 30 seconds |
| Name | `{"Name":"Bedroom"}` | Account name |
| Time zone | `{"TimeZone":"America/Los_Angeles"}` | IANA time-zone name |

Current state and legacy settings records are not interchangeable. Read the record for its fields and the reported state for device confirmation.

## Schedules, holds and shortcuts

### Weekly schedules

`POST /schedule` creates and attaches a schedule. The response was 201 with `{"scheduleId":"..."}`.

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
      "clientKey": "<client identifier>",
      "state": {
        "targetHeat": {"setpoint": 22},
        "modes": {"mode": 4}
      }
    }
  ]
}
```

Weekday and time encoding are described in the [cloud guide](../custom_components/mysa_local/protocols/cloud/README.md#requests-and-state). Schedule times follow the device's `TimeZone`; the client does not convert from HA's time zone. `clientKey` identifies the submitting client.

Read schedules with `GET /schedule?homeId={home}&includeLegacyBaseboard=true&includeLegacyAc=true&includeLegacyInfloor=true`. It returns a list of objects with `id`, `name`, `reusable`, `homeId`, `deviceType`, `deviceIds`, `events`, `createdAt` and `updatedAt`. No schedule returned 404; Python converts that to an empty list.

The Lite's legacy schedule adapter normalizes responses: generated names become
`Schedule`, weekdays become integers, events are sorted Sunday-first, and
`clientKey` becomes `v2-migration`. Compare event behavior rather than requiring
the submitted and returned JSON to match exactly.

`DELETE /schedule/{scheduleId}/attach/{dev}` detaches the device. For an unshared
schedule, the response was "Schedule unattached from device and deleted".
A route named `UPDATE_SCHEDULE /schedule/` exists, but its in-place edit behavior has
not been validated. Mysa Local replaces schedules by detaching and recreating
them; these calls are non-atomic. Its [schedule implementation](../custom_components/mysa_local/schedule.py)
backs up the original and reconciles ambiguous failures before restoring it.

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

Mysa Local requires an active schedule for a hold. Its handling of a creation-time 404 as "hold needs schedule" is not hardware-verified.

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

The PATCH returned 202. Treat `DeviceActions` as the replacement action map. Use `{"DeviceActions":{},"AllDevices":true}` to clear the action map.

Apply with `POST /homes/shortcut/apply/{shortcutId}` and `{"Timestamp":<Unix seconds>}`; the response was `Ok`. The Python client implements reading and editing shortcuts for account cleanup. It has no apply wrapper.

## Telemetry and history

### Usage

`POST /telemetry/usage/{dev}` accepts a time range and requested metrics. A usage request has this shape:

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
| --- | --- |
| `summary.a.oH1` | Heating on-time in seconds |
| `summary.a.t` / `h` / `n` | Average temperature / average humidity / sample count |
| `aggregatedReadings[].T` | Start of a 30-minute bucket |
| `aggregatedReadings[].a` | Bucket metrics, using the summary field names |
| `rawReadings[].t` | Sample time; raw readings were about 30 seconds apart |
| Raw `c`, `h`, `s` / `hs` | Room temperature, humidity and setpoint |

The reference client requests raw readings only for a one-day range. The Python `get_usage()` wrapper currently asks only for `oH1` and `t` summaries and buckets; it does not request raw history. It exposes daily runtime and average temperature through `UsageDay`.

### Humidity and Lite energy data

The telemetry endpoint returns Lite usage data even when the client UI hides it.

The Lite reports humidity despite `humiditySensor: false`, and its HomeKit
service has no humidity characteristic. A comparison with a room sensor was
consistent with compensation for board heating, but does not establish
calibration accuracy.

The usage response omitted `k` on the Lite. It has no current sensor, but `oH1` supplies heater runtime. An estimate using configured heater wattage is:

```text
energy_kWh = heating_seconds × heater_watts / 3_600_000
```

HA can fetch the current local day's cloud runtime every 15 minutes when enabled for that thermostat. Its local runtime tracker also integrates heating state and can be raised to the cloud total. These are runtime-based estimates, not measured Lite power consumption.

### State change history

`GET /state/history/{dev}?startTime=<Unix seconds>&endTime=<Unix seconds>` returned an object keyed by field, including `heatSetpoint`, `coolSetpoint` and `mode`. Each field has a list of `{timestamp,value,source}` entries.

Observed sources were `device`, `app`, `schedule` and `shortcuts`. The endpoint can distinguish wall-button changes from scheduled or app changes. Python returns these as `StateChange` objects sorted oldest first.

The legacy endpoint `POST /energy/setpoints/device/{dev}`, accepts `{"Scope":"Day","Timestamp":<Unix seconds>,"PhoneTimezone":"America/Los_Angeles"}` and returns setpoint changes with `Prev`, `Next`, `Source` and `SourceTime`.

## Model-specific controls

### Mode and heater enums

The following values are `CONTROL_MODE_TYPE`, used in `modes.mode`. Capability documents use their names:

| Value | Name | Setpoint section |
| --- | --- | --- |
| 0 | off | None |
| 1 | auto | `targetAuto` |
| 3 | cool | `targetCool` |
| 4 | heat | `targetHeat` |
| 5 | emergencyHeat | `targetHeat` |
| 6 | preCool | No established mapping |
| 7 | fanOnly | None |
| 8 | dry | None |
| 9 | sleep | None |

Python defines these mode values, but its setpoint mapping implements heat, emergency heat and cool only. It has no `targetAuto` range implementation; other modes fall back to the heat setpoint in the current callers.

`DutyCycleOpt` is a different enum: 0 is BB1 baseboard, 4 BB1 radiant, 5/6/7 BB1 fan forced short/medium/long, 8 BB2 fan forced and 9 BB2 baseboard. The Lite uses the BB2 values. The reference client enables heater-type selection for V2 and Lite from firmware 3.17.0; that gate is not a hardware-verified minimum.

### AC fan speed

The AC mapping bridges legacy remote definitions and current state values. It is implemented in Python but remains untested on AC hardware.

The device record's `SupportedModes` lists fan speeds in the legacy `Fan` enum. Convert those values to the current `modes.fan_mode` enum before writing:

| Meaning | Record `Fan` value | Shadow `fan_mode` |
| --- | --- | --- |
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

Here, shadow fan 2 is medium and shadow mode 3 is cool. Other capability-based models use `CONTROL_FAN_SPEED`: 0 off, 1–7 speeds 1–7, 8 sleep and 9 auto. Those models use `fan_mode` alone. Do not use that generic enum for AC.

### AC swing and supported remote commands

`CONTROL_SWING` is 0 invalid, 1 off, 2 on, 3 auto, 4–10 positions 1–7, 11 toggle and 12 not available. State writes need no fan conversion or current mode:

```json
{"source":3,"modes":{"verticalSwingState":2}}
```

Horizontal swing uses `horizontalSwingState`. Toggle-only remotes use `verticalSwingToggle` / `horizontalSwingToggle` with `SWING_TOGGLE_MODES` 1 off and 2 on. Mysa Local implements explicit states, not toggle-only commands.

The remote's supported controls come from its `GET /devices` record:

- `SupportedModes` is keyed by legacy mode strings: `"1"` off, `"2"` auto, `"3"` heat, `"4"` cool, `"5"` fan and `"6"` dry. Each entry has `temperatures`, `fanSpeeds`, `verticalSwing` and `horizontalSwing`.
- `SupportedKeys` and `modifiedKeys` contain IR key IDs. With a nonempty `modifiedKeys`, only fan speeds whose key is present remain available: auto 8; low/speed 1 → 9; medium/speed 2 → 10; high/speed 3 → 11; speeds 4–7 → 16–19; sleep → 20.
- The same customization removes a swing direction if none of its keys remain. Vertical keys are 12/39/40/47; horizontal keys are 15/41/42.
- `SupportedCapsVersion: "1.2"` identifies the newer remote format with numbered speeds.

Fan selection is unavailable while off in the reference client. Python applies the current-mode and customized-key filters in `DeviceRecord` and converts fan values on reads and writes. Fan and swing writes use the cloud.

### Remote temperature tracking

The newer sensor-linking flow uses `GET/POST /linking` with `{sourceId,targetId,weight}`. The reference client exposes it for Central HVAC. A remote-tracking write is:

```json
{"source":3,"tracking":{"tracking":2,"remoteTTL":480}}
```

Tracking modes are 0 ambient, 1 probe and 2 remote; 480 seconds is the reference TTL. The request does not submit a replacement temperature. The cloud supplies the linked sensor's readings.

The Lite advertises `sensing.temperature.sensors.remote: false`, `trackingSensor.validValues: ["internal"]` and no external temperature probe connector. `GET /linking?targetId={dev}` returned an empty list. A direct remote-tracking write reached `desired`, but no `tracking` value appeared in `reported`, in either normal or live mode.

In contrast, `ambientOffset` changes reached reported state within about a
second and changed both cloud and HomeKit room temperature. Offset adjustment
is supported; remote-sensor substitution on the Lite is not established.

## Adding and removing devices

For Wi-Fi provisioning and its relationship to local pairing and account enrollment, see the [Bluetooth reference](../custom_components/mysa_local/protocols/BT/README.md#scope-and-compatibility). The sections below cover cloud account operations.

### Claiming an older model

This flow applies to Baseboard V1/V2/Lite, In-floor and AC. Account claims use
`POST /devices`; the device need not already be verified online. The reference
setup flow then allows up to four minutes for the Lite's legacy-echo check.

All claims contain `Id`, `Model`, `Name`, `Home`, `TimeZone` and `LastPaired`.
`Home` is a home ID, or `"PROPERTY_HOME"` in the legacy property-manager flow.
The supplied name, home and time zone are written to the account record.

| Claim style | Additional fields and behavior |
| --- | --- |
| Minimal legacy claim | AC adds `SetupRequired: true`; Lite adds `schedGlobalOffset: 0` |
| Current legacy-device setup | Adds settings defaults such as `Animation: "off"`, `ecoMode: 1`, and `Format`; Lite also sets `DutyCycleOpt: 9` |
| Newer credential-based setup | Uses base identity/home fields after credential transfer; see the [registration sequence](#baseboard-v3-central-hvac-and-climate-sensor) |

For the current legacy-device defaults, AC adds `SetupRequired: true`; BB1 uses
`DutyCycleOpt: 0`; In-floor uses `DutyCycleOpt: 0`, `FloorOhms: 10`, `FloorType: 1`,
`IsMysaFloorSensor: true`, `MaxFloorTemp: 28`, `MaxSetpoint: 28` and `Tracking: 3`.
`ecoMode: 1` means off. These defaults can overwrite settings on re-addition.

A successful re-add returned 201. A 409 means the device is already on the
account: the existing record is returned and the submitted fields are not
written. Legacy replacement uses `POST app-prod /devices/purgeadd`, which still
worked in validation. Current setup preserves the existing record after 409
and writes `ecoMode: 1` separately through the settings endpoint.

An ownership conflict can produce "User is not allowed to access the specified
home". Its HTTP status and another-account behavior remain unverified. The
legacy-device claim body contains no possession proof beyond the device ID;
validation covered re-adding a previously owned Lite, not claiming a device
never associated with the account.

`add_device()` returns `True` on success or `False` on 409. `replace_device()`
uses `purgeadd`, where omitted settings may be reset. Both methods reject unknown
models and families requiring Bluetooth credentials.

### Deletion and re-addition

`DELETE /devices/{dev}` has no body and returned 204. Both hosts worked in the test; Python uses `mysa-backend`.

A complete account-removal workflow includes:

1. Refuse deletion in the UI unless the user is the home's admin.
2. Remove the thermostat's actions from shortcuts.
3. Detach its schedule through `/schedule/{id}/attach/{dev}`, or remove a legacy schedule through `/schedules/v2`.
4. Delete sensor/HVAC links and clear `GroupName` on grouped devices.
5. Delete the account record and remove the device from local app data.

The client deletes only the account record. HA first attempts schedule and shortcut cleanup, but continues with deletion if that cleanup fails. Sensor/HVAC-link and group cleanup are not implemented.

After deletion, the Lite disappeared from `GET /devices` and bootstrap's `AllowedDevices`. Its individual state endpoint returned 404 with "User does not have access to this device"; the batch endpoint returned `PERMISSION_DENIED` for that ID. The thermostat stayed connected to Mysa and HomeKit control kept working.

Re-adding a previously owned Lite preserved working HomeKit control and restored
cloud access. Supplied claim fields overwrite the settings record: including
`DutyCycleOpt: 9` changed the stored heater type to baseboard while reported
`controlType` remained 8. Omitted brightness, button-timing and voltage settings
returned unchanged, but that does not guarantee retention of every omitted field.

For returning devices, `add_device(app_defaults=False)` uses the minimal claim
without heater defaults. HA uses that option. Name, home and time zone are still written;
explicit `heater_type` and `extra` fields are also sent.

### Baseboard V3, Central HVAC and Climate Sensor

These cloud steps supplement the [Bluetooth credential-transfer flow](../custom_components/mysa_local/protocols/BT/README.md#credential-transfer-on-newer-models).
This newer provisioning flow is untested on hardware here and not implemented by Mysa Local.

| Stage | Request or condition |
| --- | --- |
| Register in OTA fleet | `POST /ota/fleet/devices` with `{"deviceIds":["{dev}"],"fleetName":"<fleet>"}`; fleets are `bb3-prod`, `lv-prod` and `cs-prod` |
| Claim on account | `POST /devices` with base identity/home fields, without legacy heater defaults |
| Verify cloud connection | Wait up to five minutes for a newer `identity` timestamp from `GET /state/{dev}` |
| Finalize pairing | `POST /pairing/finalize` with `{"deviceId":"{dev}"}` |
| Obtain Matter setup code | `GET /matter/qr/{dev}` returns `{qrCode,pairingCode}` |

Registration follows credential transfer at the "register device" setup stage. Subsequent
firmware management uses the [newer OTA service](#newer-ota-service).

### Temporary Wi-Fi access point setup

For Bluetooth setup, use the [protocol reference](../custom_components/mysa_local/protocols/BT/README.md)
or [Wi-Fi setup page](https://sokolskynikita.github.io/mysa-wifi-setup/).
SoftAP is a separate local HTTP provisioning path:

The setup server is `http://192.168.4.1:57961`. Read `/pubkey`, `/hwid` and
`/model`, then send `GET /network/` with an RSA-encrypted JSON `{ssid,pass}`
value in the `mysa-data` header. Poll `/connected/` (200 joined, 202 pending,
404 failed), then call `/reboot/`.

The earlier client encoding JSON-quotes the ciphertext header and also calls
`/identify`. The later encoding removes line breaks and sends the ciphertext
unquoted. These are client-format variants; their firmware compatibility has
not been established here.

On two Lite units in normal Wi-Fi mode, port 57961 refused connections. The only open TCP port found was HomeKit on 57959. An unauthenticated HomeKit HTTP request returned 470. These observations do not establish a usable local REST control API.

### HomeKit Wi-Fi switching

HomeKit Wi-Fi migration did not succeed on the tested Lite. Wi-Fi Configuration
Control (`0x22D`) allowed operation 1 reads and accepted timed TLV8 operation 3
(fail-safe update) writes with a fresh cookie and 120-second timeout. The
sequence follows [Apple's controller implementation](https://github.com/EthanArbuckle/iPhone17-1_18.2_22C152_Restore/blob/e26ed4563f78871c59d2d96856756a65d62517e5/System/Library/PrivateFrameworks/HomeKitDaemon.framework/HMDWiFiManagementController.m#L708).

The accessory returned HAP status 0 and matching pending/restart flags, but
rejoined its original AP within 5–6 seconds without attempting the target WPA2
network. No operation 4 commit was sent. Pairing and normal control remained
functional. An accepted write therefore does not establish migration success;
the cause is unresolved, and operation 2 (simple update) remains untested.

## Firmware

### Legacy devices

For V1/V2/Lite, In-floor and AC, `GET /devices/update_available/{dev}` returns:

```json
{"update":false,"installedVersion":"3.17.5.13","allowedVersion":"3.17.5.13"}
```

Both control generations use the same availability endpoint and
`GET /devices/firmware` for installed versions. Their update triggers differ:

| Path | Trigger and completion |
| --- | --- |
| Current HTTPS | `POST /devices/{dev}/firmware-update` with no body; poll availability until `update` is false |
| Legacy MQTT | `MsgType: 6, EventType: 2`; poll the same availability endpoint, with boot messages `MsgType: 10` or MCM `msg: 61` as additional completion signals |

The phone requests an update; it does not transfer the firmware image. Reference
clients poll every 10–20 seconds and may repeat the trigger while an update is
available, with a six-minute limit in the current pairing flow. BLE credential
transfer is documented [separately](../custom_components/mysa_local/protocols/BT/README.md#credential-transfer-on-newer-models).

With no update available, the trigger returned `{"triggered":true,...}` and was
followed by an HTTPS connection from the thermostat to `devices10010.getmysa.com`.
No firmware download, reboot or version change occurred. This is consistent with
an update check; installation of an available release remains unverified, and
the encrypted connection did not reveal a firmware URL.

HA sends the trigger once, then polls every 20 seconds for up to six minutes.
Installation is exposed only when its firmware-install option is enabled.

### Newer OTA service

The newer fleet-based OTA protocol for V3, Central HVAC and Climate Sensor uses `GET /ota/update/available/{dev}`, `POST /ota/update` with `{deviceIds,versionId,requestSource:"USER",...}`, `GET /ota/update/{requestId}` and `PUT /ota/update/{requestId}/cancel`. Fleet registration happens during pairing.

The Lite returned 404, "No fleet found containing device", from this availability endpoint. Python does not implement the new OTA service.

## Additional endpoints

These additional routes have no dedicated Python client methods unless described earlier.

| Request | Finding |
| --- | --- |
| `GET /homes` | Tested home had `Zones: []` |
| `GET /devices/{dev}/telemetry_alerts` | Lite returned `{"Items":[]}` |
| `GET /smart-early-on/{dev}/availability` | Lite returned `available:false` with `model-not-eligible:BB-V2-0-L`; the server enforces this restriction |
| `POST /energy/v3/{home}/home` | Whole-home energy returned 404 on the Lite account |
| `GET /homes/{home}/savedSchedule` | Returned `{"SavedSchedules":[]}` |
| `GET /schedule/compatibility/{dev}` | Returned `[]` |
| `GET app-prod /devices/state` | Legacy state: timestamped `CorrectedTemp`, `SensorTemp`, `HeatSink`, `Humidity`, `SetPoint`, `Duty`, `OnTime`, `OffTime`, `Lock`, `Brightness`, `Mode`, `TstatMode`, `ScheduleMode`, `HoldTime`, `Rssi`, `Connected`, `Current`, `Voltage`, `FreeHeap` |
| `GET /feature-flags/features/check?versionNumber={clientVersion}` | Returns `{"features":{"<NAME>":{"value":true}}}`; all returned flags in the test were true except `HIDE_MATTER_IN_APP` |
| `POST /network-diagnostics/test` | Uploads the phone's `WifiConnection`, `SpeedTest` and `MysaConnectivity` results |
| `GET /network-diagnostics/history` | Reads those uploads; neither diagnostics route probes the thermostat |
| `PATCH /users/{user}` | Sign-in metadata: `LastAppVersion`, `OperatingSystem`, `DeviceType`, `LanguagePreference` |
| `POST app-prod /users/pushNotification` | Registers a push token |

Other GET routes seen include `/homes/{home}/geofences`, `/homes/reminders/{home}`, `/homes/{home}/users`, `/invite/users/{user}`, `/devices/{dev}/drstate`, `/communication-preferences/` and notification preferences under `/preferences/User_{user}_Home_{home}/Notifications`.

Model and firmware flags include `LV_ACCESS`, `LV_AUTOMODE_FW`, `LV_ADAPTIVE_BRIGHTNESS_FW`, `LV_STAGED_AUX_HEAT_FW`, `LV_FREEZE_PROTECTION_ENABLE_FW`, `LV_TEMP_DIFFERENTIAL_FW`, `LV_EXTERNAL_PROBE_FW`, `BB3_SMART_EARLY_ON_ACCESS`, `ENROLL_BB3_DR`, `SMART_EARLY_ON_V2_ACCESS`, `ADAPTIVE_BRIGHTNESS_ACCESS`, `STAGED_AUX_HEAT_ACCESS` and `DR_ENROLMENT_WEB_FLOW_ACCESS`. Their presence in a response does not establish that a particular thermostat supports the feature.

Additional legacy routes include `/energy/device|zone|home/{id}`,
`/batchReadings/v2/{dev}`, `/schedules`, `/schedules/device/update/{dev}` and
`/devices/capabilities`; their continued availability has not been tested.
Property-manager requests use `GET /zones` on `dash-api-v1.mysa.cloud`.
`/users/readingsForUser` is a defined but unverified route. Legacy NFC setup
uses `GET /nfc/auth-key`; it has no identified role in current provisioning.

## Integration behavior

Polling, fallback, persistence and UI behavior belong to the integration rather
than the API. See the [architecture guide](architecture.md#home-assistant-integration-layer)
and [integration guide](../README.md) for those policies. Client structure and
test coverage are documented in the [cloud protocol guide](../custom_components/mysa_local/protocols/cloud/README.md).

## Sources and validation

Current HTTPS, newer provisioning/OTA, and later SoftAP behavior were derived
from Android app **4.20.1**; legacy REST/MQTT and earlier SoftAP behavior were
compared with **3.54.1**. These app releases are evidence sources, not protocol
version identifiers. For requests containing `{clientVersion}`, the validated
value is the current-client release cited here; `clientKey` is client metadata.
Hardware validation on October 10–11, 2026 used Baseboard Lite (`BB-V2-0-L`),
firmware `3.17.5.13`. AC and newer-model flows remain code-derived unless stated
otherwise; firmware installation with an available update was not verified.
