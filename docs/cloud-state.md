# Cloud state, history, and model fields

The [cloud protocol reference](../custom_components/mysa_local/protocols/cloud/README.md) owns endpoint availability, S/R/L terminology, observed limitations, and recommended client policy. This page supplies detailed wire mechanics; implementation status belongs to the [cloud tracker](implementation/cloud.md).

The protocols were reverse-engineered from the original Mysa apps. Hardware observations cover Baseboard Lite `BB-V2-0-L`, firmware `3.17.5.13`. Other-model behavior remains app/code-derived unless explicitly stated.

- [Discovery and capabilities](#account-discovery-and-capabilities)
- [State and liveness](#reading-and-writing-thermostat-state)
- [Usage and history](#telemetry-and-history)
- [Model-specific controls](#model-specific-controls)

## Account discovery and capabilities

`GET /mobile/bootstrap?versionNumber={clientVersion}` returns account data and routing metadata. `clientVersion` is the mobile-client version, not an API version; use `APP_VERSION` from the [client constants](../custom_components/mysa_local/protocols/cloud/const.py). Omitting `versionNumber` returned 400 with `FST_ERR_VALIDATION`. The response has `meta` and `data` objects: home selection is in `meta.primaryHomeId` / `activeHomeId`; `data.user` contains `Id`, `AllowedDevices`, `AllowedHomes` and Cognito attributes. The bootstrap fixture also records `data.homes` and `data.devices`.

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
possible for individual IDs. `get_states()` returns successful entries and records per-device errors in
`source_errors`. The coordinator retains previous missing observations and reads
legacy `/devices/state` independently. Each selected field retains its own source
and clock; the fetch time never makes a stale measurement fresh.

Online status can remain true while readings are stale. Assess each field/source timestamp rather than trusting the connection flag; the
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

[Temporary live-reporting request](examples/cloud/live-mode.request.json)

The response has `message`, `streamedDeviceIds`, `skippedDeviceIds` and `failedDeviceIds`. The examined mobile app renews a 300-second stream about every 4.2 minutes. Live mode changes the thermostat's reporting rate; the client still has to poll state.

The recorded cloud liveness check compared reading timestamps before and after
a short stream (`timeout: 20`), polling `GET /state/{dev}` for advancement. The first fresh reading arrived about one second after that request. Readings stopped when the short stream expired. A returned ID in `streamedDeviceIds` alone does not confirm that the thermostat responded.

Same-value writes are also unsuitable as a ping. Writing an unchanged offset or brightness pair returned 200 without advancing `reported.timestamp`.

### State and settings confirmation

Use the primary reference's [write matrix](../custom_components/mysa_local/protocols/cloud/README.md#write-matrix)
and [recommended confirmation policy](../custom_components/mysa_local/protocols/cloud/README.md#recommended-reconciliation-of-cloud-writes).
Send both brightness values together. Lite offset and setpoint limits use 0.5 °C
steps; offset is bounded to −5 through +5 °C.

Earlier observations saw mode, lock, brightness, limits, and offset reach reported
state in about one second. Later setpoint/mode tests differed between the two
Lites. Earlier R heater-profile writes changed REC without changing ST within
30 seconds; later tests established reported changes for both R and S encodings.
Display units changed locally while ST initially remained `C`. These bounded
observations are compatible with lag and differing device behavior; an accepted
write or REC echo alone does not establish physical application.

## Telemetry and history

### Usage

`POST /telemetry/usage/{dev}` accepts a time range and requested metrics. A usage request has this shape:

[Expanded usage request](examples/cloud/usage.expanded.json)

The response is a list of local-day objects. Each has a day timestamp `T`, a `summary` and `aggregatedReadings`; requested raw data appears under `rawReadings`.

| Location | Meaning |
| --- | --- |
| `summary.a.oH1` | Heating on-time in seconds |
| `summary.a.t` / `h` / `n` | Average temperature / average humidity / sample count |
| `aggregatedReadings[].T` | Start of a 30-minute bucket |
| `aggregatedReadings[].a` | Bucket metrics, using the summary field names |
| `rawReadings[].t` | Sample time; raw readings were about 30 seconds apart |
| Raw `c`, `h`, `s` / `hs` | Room temperature, humidity and setpoint |

The examined mobile app requests raw readings only for a one-day range. The Python `get_usage()` wrapper currently asks only for `oH1` and `t` summaries and buckets; it does not request raw history. It exposes daily runtime and average temperature through `UsageDay`.

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

`DutyCycleOpt` is a different enum: 0 is BB1 baseboard, 4 BB1 radiant, 5/6/7 BB1 fan forced short/medium/long, 8 BB2 fan forced and 9 BB2 baseboard. The Lite uses the BB2 values. The examined mobile app enables heater-type selection for V2 and Lite from firmware 3.17.0; that gate is not a hardware-verified minimum.

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

[AC fan request](examples/cloud/state-update.ac-fan.json)

Here, shadow fan 2 is medium and shadow mode 3 is cool. Other capability-based models use `CONTROL_FAN_SPEED`: 0 off, 1–7 speeds 1–7, 8 sleep and 9 auto. Those models use `fan_mode` alone. Do not use that generic enum for AC.

### AC swing and supported remote commands

`CONTROL_SWING` is 0 invalid, 1 off, 2 on, 3 auto, 4–10 positions 1–7, 11 toggle and 12 not available. State writes need no fan conversion or current mode:

[AC swing request](examples/cloud/state-update.ac-swing.json)

Horizontal swing uses `horizontalSwingState`. Toggle-only remotes use `verticalSwingToggle` / `horizontalSwingToggle` with `SWING_TOGGLE_MODES` 1 off and 2 on. Mysa Local implements explicit states, not toggle-only commands.

The remote's supported controls come from its `GET /devices` record:

- `SupportedModes` is keyed by legacy mode strings: `"1"` off, `"2"` auto, `"3"` heat, `"4"` cool, `"5"` fan and `"6"` dry. Each entry has `temperatures`, `fanSpeeds`, `verticalSwing` and `horizontalSwing`.
- `SupportedKeys` and `modifiedKeys` contain IR key IDs. With a nonempty `modifiedKeys`, only fan speeds whose key is present remain available: auto 8; low/speed 1 → 9; medium/speed 2 → 10; high/speed 3 → 11; speeds 4–7 → 16–19; sleep → 20.
- The same customization removes a swing direction if none of its keys remain. Vertical keys are 12/39/40/47; horizontal keys are 15/41/42.
- `SupportedCapsVersion: "1.2"` identifies the newer remote format with numbered speeds.

Fan selection is unavailable while off in the examined mobile app. Python applies the current-mode and customized-key filters in `DeviceRecord` and converts fan values on reads and writes. Fan and swing writes use the cloud.

### Remote temperature tracking

The newer sensor-linking flow uses `GET/POST /linking` with `{sourceId,targetId,weight}`. The examined mobile app exposes it for Central HVAC. A remote-tracking write is:

[Remote-tracking request](examples/cloud/state-update.remote-tracking.json)

Tracking modes are 0 ambient, 1 probe and 2 remote; 480 seconds is the reference TTL. The request does not submit a replacement temperature. The cloud supplies the linked sensor's readings.

The Lite advertises `sensing.temperature.sensors.remote: false`, `trackingSensor.validValues: ["internal"]` and no external temperature probe connector. `GET /linking?targetId={dev}` returned an empty list. A direct remote-tracking write reached `desired`, but no `tracking` value appeared in `reported`, in either normal or live mode.

In contrast, `ambientOffset` changes reached reported state within about a
second and changed both cloud and HomeKit room temperature. Offset adjustment
is supported; remote-sensor substitution on the Lite is not established.
