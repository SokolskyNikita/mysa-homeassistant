# Experimental cloud support for newer models

Baseboard V3 (`BB-V3-0`), Central HVAC (`ST-V1-0`, `ST-V1`, `LV`) and Climate
Sensor (`CS-V1-0`, `CS-V1`) can be imported from an existing Mysa account.
Complete Wi-Fi setup, account enrollment and equipment configuration in Mysa's
app first. Then sign in through Mysa Local and select the devices. No helper,
credential copy, HomeKit pairing or Matter setup is needed for cloud control.

These models have **experimental, app-derived support**. They have not been
validated on hardware here. The untested-model setup notice, device model line
and integration diagnostics identify this status.

## Implemented controls

| Family | Entities and controls |
|---|---|
| Baseboard V3 | Heat/off, setpoint, temperature, humidity, connectivity; screen units, brightness and button lock when advertised |
| Central HVAC | Advertised heat/cool/off/fan-only modes; heat/cool auto with separate low/high targets when enabled; temperature, humidity, equipment action; basic fan on/auto/off and native fan speeds when advertised; supported display controls |
| Climate Sensor | Temperature, humidity and connectivity; Wi-Fi signal when reported. No climate entity or heating controls |

A missing capability document never creates default heating controls on these
models. Entities can appear after a later successful capability refresh. Missing
or invalid data does not invent measurements; stale readings become unknown or
unavailable. Commands retain the existing pending/confirmed distinction: acceptance by Mysa's server does not
immediately replace the displayed device state.

Central HVAC's Home Assistant `heat_cool` mode maps to Mysa AUTO. A range command
writes `targetAuto.heatSetpoint` and `targetAuto.coolSetpoint` together. Ordinary
heat and cool still use `targetHeat.setpoint` and `targetCool.setpoint`. Mode and
targets supplied together are validated before one state update. Limits are
checked separately for each target, including reported lockouts and the minimum
heat/cool gap. A single target is rejected in AUTO; supply both range values.

HVAC activity uses fresh `modes.reported.hvacStates`, or fresh telemetry
`hvacState` stage bits. It does not infer heating from the selected mode or from
baseboard duty cycle. Missing activity data gives unknown action.

The fan operation control is separate from the optional fan-speed selector.
Native speed AUTO is `9`; the Mini-Split AC mapping is different. A circulation
program configured in Mysa's app may be displayed as `runtime`. Editing that
runtime program is not implemented; selecting on/auto/off replaces it using the
app's basic fan-operation settings.

## Deferred features

Schedules/holds, runtime/energy entities, remote sensor linking, equipment
configuration, emergency-heat controls, adaptive-display settings, demand-response enrollment and newer
fleet OTA are outside this implementation. Legacy usage, schedule-hold, firmware
and device-settings routes are excluded for these families. Manage enrollment,
removal, advanced settings and updates in Mysa's app.

The separate Wi-Fi pairing page still supports its existing models. This work
adds no Bluetooth credential-transfer or Matter implementation.

## Evidence and automated tests

[Fixtures](../tests/fixtures/app_4201_new_models.json) contain **synthetic test
scenarios**, not captured device responses. Field paths and enum encodings come
from the locally decompiled Mysa Android 4.20.1 app. Capability values, equipment
combinations and example readings are constructed for tests. The inspected
`assets/index.android.bundle` matches the saved APK and has SHA-256
`1cf8a5c0fd83fd1e2a355abd9d22fb70434ca023140d18bc56f5be4927538dde`.

Relevant app code includes `CAPABILITY_PATHS`, `TELEMETRY_PATHS`,
`decodeHvacState`, `getHeatCoolStagesFromModes`, `getValidFanSpeeds`,
`setFanAlwaysOn`, `setMinimumFanRuntime`, and the non-legacy AUTO setpoint branch.
`decodeHvacState` uses heat bits 256/512, auxiliary heat 4096, cool 1024/2048 and
fan stage 8192. Fan operation writes `hvacConfig.advFanAlwaysOn`,
`advFanRuntimeSeconds` and `advFanRuntimePeriodSeconds`. All ordinary commands
use `POST /state/{dev}/update` with `source: 3`.

[Regression tests](../tests/test_new_cloud_models.py) exercise account imports,
model-specific entities, mixed accounts with a Lite, reload, capability failures,
mode and range validation, native fan commands, source freshness, cloud errors,
delayed confirmation and diagnostics. These establish integration behavior
against the documented shapes; they cannot establish actual device application,
backend permissions or the exact capabilities returned by a particular firmware.

## First V3 hardware check

1. Set up V3 in Mysa's app and verify its basic controls there.
2. Install Mysa Local 0.5.1 or later, sign in and import V3. Confirm it shows
   experimental cloud support and does not ask for HomeKit pairing.
3. Compare temperature, humidity, current mode and target against the app. Record
   the model, firmware, integration and HA versions, and original mode/setpoint.
4. In **Settings → Devices & services → Mysa Local → ⋮**, select **Enable debug
   logging**. Reproduce once with a small setpoint change; note the time/timezone,
   requested value and actual HA/app/physical thermostat results. Allow time for
   cloud reporting; HTTP acceptance alone does not establish application.
5. Restore the original settings. Download diagnostics from the account entry's
   **⋮ → Download diagnostics**, **before any integration reload or HA restart**.
   Then **Disable debug logging** and save the offered log. Review both files for
   personal data and attach them to the model report with your notes. History is
   memory-only and resets on reload/restart; no shell or API calls are needed.
6. Repeat this capture workflow for off/heat or supported display/lock controls
   that fail. Check app-to-HA updates. Test a reload only after saving the capture.
   V3 testing is cloud-only: HomeKit discovery, pairing and local fallback are not
   prerequisites. Do not test unavailable legacy schedules/holds/settings.

See the [complete capture instructions](../README.md#capture-a-command-problem)
for retention limits and the distinction between stored, reported and applied.

Real V3 feedback is the next validation step. Central HVAC and Climate Sensor
will still need their own hardware checks afterward.
