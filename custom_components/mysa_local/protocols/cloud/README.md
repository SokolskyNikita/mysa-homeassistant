# Mysa cloud protocol

- [API varieties](#the-three-api-varieties)
- [Operations](#operations-and-relevance-to-home-assistant)
- [Read/write mappings](#readwrite-comparisons)
- [Recommended reads](#recommended-reconciliation-of-cloud-reads)
- [Recommended writes](#recommended-reconciliation-of-cloud-writes)
- [Client status](#client-status)

Supporting references: [authentication](../../../../docs/cloud-auth.md),
[state and model fields](../../../../docs/cloud-state.md),
[workflows](../../../../docs/cloud-workflows.md), and
[firmware/connectivity](../../../../docs/firmware-and-connectivity.md).

## Context and conventions

Mysa exposes overlapping cloud services: structured device state, older
telemetry, and stored account/device settings. A successful request does not
necessarily mean fresh data or an applied thermostat command. This guide
covers the useful operations, their API mappings, and **recommended cloud-only
selection rules** for a Home Assistant client. The recommendations are not all
implemented yet; see [client status](#client-status).

HA should own automations, scenes, presence, notifications, and dashboards.
Mysa's cloud remains useful for device control, hardware settings, runtime,
firmware, and managing existing Mysa schedules that could affect HA commands.
HomeKit/cloud reconciliation is outside this document.

The three access paths used below are:

| Shorthand | API variety | HTTPS base URL |
| --- | --- | --- |
| **S** | Structured/current services | `https://mysa-backend.mysa.cloud` |
| **R** | Legacy REST through the modern host | `https://mysa-backend.mysa.cloud` |
| **L** | Legacy REST through the older host | `https://app-prod.mysa.cloud` |

**R and L are two entry points to overlapping services, not independent
thermostat measurements.** The distinction matters for route availability;
S and R share a hostname but have different response formats.

Notation: `{dev}` is the account's device ID, `{home}` its home ID, and `{id}`
the resource ID returned by creation/discovery. **ST** means S's `reported`
state sections, **TEL** its `latestTelemetry.reading`, **LT** the legacy
`DeviceStatesObj[dev]` telemetry, and **REC** the device's settings record.
Temperatures and setpoints are Celsius regardless of screen units. Decode
mode, lock, and fan enums by API/model before comparing them.

The live findings apply to two Baseboard Lites (`BB-V2-0-L`, firmware
`3.17.5.13`), checked October 10–12, 2026. Other-model mappings are identified
as such. This is a reverse-engineered protocol, not a vendor API contract.

### Shared authentication

All three paths use the **same Cognito ID token**, sent raw in
`authorization` with **no `Bearer` prefix**. Send `accept: application/json`,
the integration's `user-agent`, and `content-type: application/json` for JSON
bodies. No separate login or AWS request signing is needed for R or L.

Use `MysaAuth.sign_in(email, password)` for the Cognito SRP exchange, or
initialize it with a saved refresh token. `async_get_id_token()` handles
refresh; retain a replacement refresh token through `on_refresh_token` when
one is returned. The public Cognito identifiers are in [`const.py`](const.py),
and the exchange is described in the [authentication reference](../../../../docs/cloud-auth.md#cognito-sign-in).
Credentials and token persistence belong to the caller.

<a id="requests-and-state"></a>

## The three API varieties

### S — structured/current services

Use the modern base URL and shared authentication above. Discover the account
with `GET /mobile/bootstrap?versionNumber={APP_VERSION}`, using `APP_VERSION`
from `const.py`, then request state for the discovered IDs:

[Batch-state exchange with existing response fixture](../../../../docs/examples/cloud/state-batch.success.json)

A batch returns `{device_id: {"data": state}}`; inspect individual entries for
failure. `GET /state/{dev}` returns one state object directly. ST contains
configuration/control sections such as `targetHeat`, `modes`, and
`physicalInterface`; TEL contains measurements. Where a section has
`desired` and `reported`, only `reported` is evidence of applied state.

Commands use section changes with `source: 3`:

[Setpoint request and accepted reply](../../../../docs/examples/cloud/state-update.setpoint.json)

This requests a setpoint change; it does not select heat mode. Source 3 appears
as `app` in change history. Current capability, usage, schedule, hold, and
live-mode routes are listed in the comparison tables. S's state routes return
403 on L; changing the hostname does not provide a second structured API.

### R — legacy REST on the modern host

Use the same modern base URL, ID token, and headers. Two distinct reads matter:

- `GET /devices/state` returns LT: `DeviceStatesObj[dev]`, generally with
  independent `{v, t}` pairs for each field.
- `GET /devices` returns `DevicesObj[dev]` settings records; `GET /devices/{dev}`
  reads one record. REC describes stored configuration, not live measurements.

Settings writes use `POST /devices/{dev}/` with the fields to change; preserve
the trailing slash. For example, `{"Format":"fahrenheit"}` changes the
thermostat's screen setting. The response can echo stored values without
proving application. Legacy history and schedule endpoints also use R.

### L — legacy REST on the older host

Use `https://app-prod.mysa.cloud` with the **same authentication and request
formats as R**. For example, `GET /devices/state` and
`POST /devices/{dev}/` work on both. Treat L as a compatibility route for
specific operations, not an additional source to poll continuously.

Availability is operation-specific: saved-schedule event editing works on L
but returns 404 on R; some R discovery routes and current services return 403
on L. The client's existing `replace_device()` uses L's `/devices/purgeadd`.
Do not replace the entire client's base URL to obtain legacy telemetry.

## Operations and relevance to Home Assistant

This is the consolidated operation inventory, independent of API flavor.
**Include** means a normal client capability; **Consider** means optional or
model-specific; **Skip** means no dedicated new client method or routine HA
polling. Existing maintenance helpers can remain even when a full app feature
is not recommended. Read/write labels describe the operation, not a host.

| Operation | What it does | HA recommendation |
| --- | --- | --- |
| Account/device discovery · read | Finds homes, device IDs, models, names, and membership | **Include** the discovery needed for setup; do not build account administration |
| Capabilities · read | Describes supported modes, controls, ranges, and model features | **Include**; combine with observed valid readings, rather than trusting every flag |
| Room temperature / humidity · read | Reports ambient conditions | **Include**; climate and humidity entities |
| Setpoint / operating mode · read/write | Selects the requested temperature and heating/cooling/off mode | **Include**; core climate controls |
| AC fan / swing · read/write | Selects fan speed and vent direction | **Include when supported**; honor the AC remote's available commands |
| Heating activity / runtime · read | Reports recent duty and accumulated heating time | **Include**; distinguish interval activity from an instantaneous relay state |
| Measured electrical data · read | Reports power/current/energy on hardware that measures it | **Consider by model**; skip Lite's zero/unmeasured fields; label calculated energy as an estimate |
| Setpoint limits · read/write | Restricts temperatures accepted at the thermostat | **Include**; hardware constraints remain useful with HA automations |
| Temperature offset · read/write | Calibrates the thermostat's ambient reading/control | **Include**; do not substitute an HA display-only correction |
| Button lock · read/write | Locks the physical controls | **Include** as device configuration |
| Wake / idle brightness · read/write | Sets the physical display's two brightness levels | **Include** as device configuration |
| Display units · read/write | Chooses Celsius/Fahrenheit on the thermostat screen | **Include**; independent of HA's display units |
| Heater profile · read/write | Selects the installed heater's switching profile | **Include** as installation configuration, not a routine automation target |
| Floor limits/probe, adaptive display, remote tracking, HVAC configuration · read/write | Configures model-specific sensors and control behavior | **Consider only on matching hardware**; Lite cannot validate these controls |
| Identity / installed firmware · read | Identifies hardware and current firmware | **Include** as device metadata |
| Device name / time zone · read/write | Stores Mysa's label and schedule time zone | **Read** for discovery/schedule interpretation; **skip cloud rename**, consider time-zone writes only for explicit Mysa schedule setup |
| Firmware availability / update · read/write | Checks for an offered version and requests installation | **Include**; user-initiated update, not an automatic consequence of polling |
| Cloud connection / data age · read | Describes API reachability and thermostat reporting freshness | **Include** separate health/freshness diagnostics |
| RSSI / internal temperatures / free heap · read | Helps diagnose radio, sensor, or firmware behavior | **Consider** diagnostics, disabled by default; not extra climate readings |
| Live reporting · write | Temporarily asks devices to upload more frequently | **Consider** for cloud responsiveness and troubleshooting; not a permanent background stream by default |
| Usage / reading history · read | Supplies runtime totals and past measurements | **Include runtime**; **consider** history for gaps/troubleshooting, not a duplicate HA recorder |
| State-change history · read | Explains what changed a target or mode | **Consider** on demand when Mysa and HA behavior disagree |
| Device schedules · read/write | Stores timed thermostat actions | **Include inspection/removal** of conflicting existing schedules; **exclude** in-place schedule editing as a client feature. Prefer HA scheduling |
| Hold / resume · read/write | Overrides a Mysa schedule temporarily or permanently, then resumes it | **Include when a Mysa schedule exists**; HA-only scheduling does not need a cloud hold for every command |
| Home/Away/Vacation shortcuts · read/write | Applies a stored bundle of device actions | **Consider inspection/cleanup** for coexistence; **skip new shortcut creation/apply UI**, use HA scenes |
| Demand-response state · read | Indicates a utility event that may affect behavior | **Consider** a read-only explanation/status; skip enrollment management |
| Device claim / replacement / removal · write | Changes the device's association with a Mysa account | **Consider** explicit setup/recovery tools only; not normal thermostat control |
| Saved schedule templates · read/write | Stores reusable schedules and applies copies to devices | **Skip** template management; recognize applied device schedules through the schedule operation above |
| Mysa zones / aggregate energy · read/write | Groups devices and summarizes a Mysa home/zone | **Skip**; use HA areas/groups and validated energy sources |
| Geofences / crossings · read/write | Runs Mysa actions when a phone enters or leaves | **Skip**; use HA presence and automations |
| Alerts / reminders / notifications · read/write | Stores thresholds, reminders, push tokens, and notification preferences; sends notifications | **Skip**; use HA automations and notification services |
| Mysa account/home/access management · read/write | Edits profiles/homes, transfers access, manages invitations, or deletes accounts | **Skip** beyond discovery required for setup |
| App/third-party services · read/write | Handles app flags, phone diagnostics, analytics, localization, Alexa authorization, and property-manager features | **Skip**; they do not improve thermostat control in HA |
| Obsolete provisioning helpers · read/write | Supplies legacy NFC or anonymous AC-setup credentials | **Skip**; separate from an authenticated HA thermostat client |
| Mysa eco / Early On / animation settings · read/write | Supplies app-specific energy/schedule behavior and display animation | **Skip new methods**; prioritize HA scheduling and the explicit hardware controls above. Early On is unavailable on Lite; eco/animation effects were not established |

## Read/write comparisons

`—` means no applicable mapping in our evidence, not proof that no future
endpoint could expose it. **Reported** means the changed value appeared in ST;
**applied** means thermostat behavior independently confirmed it. **Stored**
means only a record round trip was established. Model-specific entries below
come from app/code mappings, not Lite hardware validation.

### Read matrix

R and L cells name the data source, not just a similarly named field. `= R`
means the same route/format is available; it is not another vote for a value.

| Value | S | R | L |
| --- | --- | --- | --- |
| Room temperature / humidity | TEL `roomTemperature` / `humidity` | LT `CorrectedTemp.v` / `Humidity.v` | = R |
| Internal temperatures | TEL `rawTemperature`, `coreTemperature` | LT `SensorTemp.v`, `HeatSink.v` | = R |
| Heating target | ST `targetHeat.setpoint`; TEL `heatSetpoint` | LT `SetPoint.v` | = R |
| Operating mode | ST `modes.mode`; TEL `mode` uses a different enum | LT `Mode.v`, `TstatMode.v`, each with its own enum | = R |
| Cooling / AC controls | ST `targetCool.setpoint`, `modes.fan_mode`, `verticalSwingState`, `horizontalSwingState` | REC `SupportedModes` / `SupportedCaps` describes available commands, not current state | = R formats; other-model support not live-verified |
| Minimum / maximum target | ST `targetHeat.lockoutMin` / `lockoutMax` | REC `MinSetpoint` / `MaxSetpoint` | = R |
| Button lock | ST `physicalInterface.lockout` | LT `Lock.v`; REC `ButtonState` | = R |
| Wake / idle brightness | ST `physicalInterface.activeIntensity` / `idleIntensity` | REC `MaxBrightness` / `MinBrightness`; LT `Brightness.v` is only one value | = R |
| Screen units | ST `physicalInterface.format` | REC `Format` | = R |
| Temperature offset | ST `tracking.ambientOffset` | — | — |
| Heater profile | ST `bbConfig.controlType` | REC `DutyCycleOpt` | = R |
| Heating activity / interval runtime | TEL `dutyCycle`, `onTime` | LT `Duty.v`, `OnTime.v`, `OffTime.v` | = R |
| Electrical data | TEL `voltage`, `maxCurrent`, `powerConsumed`, `instantLoad`; ST `power` | LT `Voltage.v`, `Current.v`; REC `Voltage` is configuration | = R |
| RSSI / free heap | TEL `rssi`, `freeHeap` | LT `Rssi.v`, `FreeHeap.v` | = R |
| Connection / timestamps | `latestTelemetry.isConnected`, `lastConnected`; TEL `timestamp`; ST section timestamps | LT `Connected.v/t`, individual field `t`, top-level `Timestamp` | = R |
| Identity / names / time zone | ST `identity.model`, `fw`; bootstrap account data | REC `Id`, `Model`, `Name`, `Home`, `TimeZone` | = R |
| Capabilities | `GET /capabilities/{dev}` | `GET /devices/capabilities`; REC for model-specific command metadata | Capability-list route returns 403; REC still works |
| Firmware availability | — | `GET /devices/update_available/{dev}`; `/devices/firmware` lists versions | = R |
| Usage / raw history | `POST /telemetry/usage/{dev}` | `POST /batchReadings/v2/{dev}` | Same route, but bucket keys can differ |
| Change history | `GET /state/history/{dev}` | `POST /energy/setpoints/device/{dev}` for target changes | = R |
| Active schedules / holds | `GET /schedule?homeId={home}`; `/schedule/hold/{dev}` | `GET /schedules` (v1), `/schedules/v2/{dev}` (v2); LT `ScheduleMode` / `HoldTime` are hints | = R |
| Shortcuts / demand response | ST `drState` where present | `GET /homes/{home}/scenes`; `/devices/{dev}/drstate` | = R |

### Write matrix

S field paths below are sent in `POST /state/{dev}/update`; R/L settings
fields are sent in `POST /devices/{dev}/`. These are available routes, not a
recommendation to use them all.

| Operation | S | R | L |
| --- | --- | --- | --- |
| Heating target | `targetHeat.setpoint`: applied on one Lite, unconfirmed on the other | — | — |
| Heat / off | `modes.mode` = 4 / 0: same mixed application | REC `Mode` object: stored only, **not a working mode command** | = R |
| AC target / fan / swing | `targetCool.setpoint`, `modes.fan_mode`, directional swing fields; model-specific | No verified direct control mapping | = R |
| Button lock | `physicalInterface.lockout` = 3 / 0: reported | `ButtonState` = `Locked` / `Unlocked`: reported | = R |
| Wake / idle brightness | `physicalInterface.activeIntensity` / `idleIntensity`: reported | `MaxBrightness` / `MinBrightness`: reported | = R |
| Temperature offset | `tracking.ambientOffset`: reported | — | — |
| Setpoint limits | `targetHeat.lockoutMin` / `lockoutMax`: reported | `MinSetpoint` / `MaxSetpoint`: reported | = R |
| Screen units | `physicalInterface.format` = `C` / `F`: applied | `Format` = `celsius` / `fahrenheit`: applied | = R |
| Heater profile | `bbConfig.controlType` = 8 / 9: reported | `DutyCycleOpt` = 8 / 9: reported | = R |
| Device name / time zone | — | `Name` / `TimeZone`: stored | = R |
| Schedules | `POST /schedule`; full-body `PUT /schedule/{id}`; `DELETE /schedule/{id}/attach/{dev}` | v1 `POST /schedules/device/update/{dev}`; v2 `POST /schedules/v2/`, `DELETE /schedules/v2/{id}` | = R, with v1 body difference noted below |
| Hold / resume | `POST /schedule/hold`; `DELETE /schedule/hold/{dev}`: stored/removed | — | — |
| Firmware trigger | `POST /devices/{dev}/firmware-update` with JSON `{}`: accepted | — | Same trigger returns 403 |
| Live reporting | `POST /mysa-legacy/stream/home/{home}` with `{"timeout":seconds}`: accepted | — | Same route returns 403 |
| Claim / remove / replace | — | `POST /devices`, `DELETE /devices/{dev}` | Claim/delete work; existing replacement uses `POST /devices/purgeadd` |

The remaining app-oriented operations are intentionally compact here: shortcut
edit/apply, alert, zone, geofence, and reminder record operations worked on R
and L; shortcut application was inconsistent. Saved-template create/rename/
apply/delete worked on both, but event editing worked only on L (R: 404).
`ecoMode` and `Animation` settings persisted on both without verified physical
effects. These results do not justify expanding the HA client with those app
features. Other account/app administration is known from app sources, not
validated as thermostat functionality.

## Recommended reconciliation of cloud reads

The client and coordinator implement the following acquisition and selection policy;
the implementation tracker records remaining hardware validation.
Use **S + R** for normal cloud state acquisition: one S batch and one R legacy
state request at the configured cloud interval. Cache REC separately. Use L
as a replacement for a failing R route with backoff, not a third permanent
poll. Do not average values or count matching R/L responses as corroboration.

### One selection rule, applied per field

1. **Normalize.** Decode each source into the same units/enums and preserve
   `source`, `observed_at`, and `fetched_at`. Parse ISO timestamps and Unix
   seconds/milliseconds. Use the individual LT field's `t`, TEL's reading
   timestamp, or the relevant ST section timestamp; never borrow a sibling
   field's timestamp. Keep target temperature separate from an effective
   target altered by a hold or other control behavior.
2. **Validate.** Reject missing, malformed, non-finite, or semantically invalid
   values. Zero and `false` are valid where the field allows them. A timestamp
   more than one minute in the future is invalid (the existing HA guard); do not let it win a
   newest-value comparison. A valid clock on one section does not validate
   another section.
3. **Choose by the field policy below.** For **Latest** fields, choose the
   newest valid, fresh observation; ties prefer S, then R, then L. Include the
   cached observation so a delayed response cannot roll a newer value back. An
   un-timestamped measurement is not fresh merely because its HTTP response
   just arrived. For **Config** fields, use the named authority/fallback;
   their change timestamps may legitimately be old. REC is stored intent,
   never another device measurement.
4. **Keep provenance.** Retain last-known values with their original age when
   no fresh candidate exists; expose stale/unavailable state rather than
   refreshing their timestamps. A missing field or failed device in a batch
   must not erase good data for other fields/devices. Keep pending writes
   separate from confirmed readings.

For measurement freshness, a suggested starting limit is **35 minutes outside
live reporting**, because ordinary Lite uploads can be 12–30 minutes apart.
During an established live-reporting session, use **max(90 seconds, three poll
intervals)**. These are configurable policy defaults, not vendor guarantees
or a claim that a 30-minute-old temperature is real-time. A future-dated
measurement is rejected rather than having its clock silently corrected.
For a configuration field with an invalid timestamp, do not use that timestamp
to outrank another source; retain its unconfirmed/last-known status.

| Field / operation | Selection and HA behavior |
| --- | --- |
| Room temperature / humidity | **Latest** S TEL vs R/L LT. Use `CorrectedTemp`, never internal sensor temperatures. Accept valid humidity even when the Lite capability flag says false |
| Heating target / mode | Use the newest plausibly timestamped device report from ST or LT; use TEL only after matching target semantics and decoding its different mode enum. Unlike measurements, an unchanged target need not expire after 35 minutes. Without comparable timestamps prefer ST, then LT, and mark freshness unknown. Never use REC `Mode` or S `desired` as confirmation |
| Cooling target / fan / swing | **Config:** ST; REC only describes allowed choices. No verified legacy-state fallback in this guide |
| Minimum / maximum target | **Config:** ST pair, then REC pair if absent; validate minimum ≤ maximum. Never turn an uncertain/fallback value into permission to exceed known device limits |
| Button lock | Prefer the newest valid device report from ST or LT when timestamps are comparable; otherwise ST, then LT. REC is a stored-setting fallback only |
| Wake / idle brightness | **Config:** ST pair, then REC pair. LT's single brightness value cannot reconstruct both settings |
| Screen units | **Config:** REC `Format`, ST as fallback. ST has lagged the screen setting. This selects the configured screen units, not proof of physical application or HA's preferred display unit |
| Temperature offset | **Config:** ST only; absent means unknown, not zero |
| Heater profile | **Config:** ST, then REC `DutyCycleOpt` as stored configuration; validate against the device family |
| Heating activity | **Latest** normalized duty/interval observation. Keep each source's interval meaning; do not add R/L duty or runtime together or infer heater-off from Lite's zero power fields |
| Runtime / history | Prefer S usage `oH1` for runtime; R batch history only as an explicit fallback after bucket/unit normalization. Fetch bounded periods and do not sum overlapping sources. Historical samples do not become current readings |
| Identity / name / time zone / firmware | REC for identity/name/time zone; firmware information endpoint for offered/installed version, with ST identity as installed-version fallback. Cache metadata rather than treating old unchanged values as stale telemetry |
| Capabilities / model controls | S capabilities plus REC model/remote metadata. Preserve hardware limits; do not invent unsupported controls. Model-specific configuration has no general Lite fallback |
| Schedules / holds | Read S schedules and holds, plus legacy schedule sources when inspecting existing Mysa automation. An empty S list does not establish that no legacy schedule exists. Do not use REC `Schedule` or LT hold hints as the complete resource |
| Shortcuts / demand response | On-demand shortcut inspection through R/L. For demand response use ST when present, otherwise the dedicated R route; no merging of unrelated account mode fields |
| Connection / health | Keep request success and data freshness separate for S and legacy reads. A 200 or connected flag cannot make stale measurements fresh; track selected source and age per field. Do not let one failing API mark both paths failed |
| RSSI / internal temperatures / heap | Apply **Latest** only when diagnostics are requested/enabled. Discard from normal entity publication otherwise |
| Lite electrical values, unreliable energy summaries, and **Skip** operations | Do not request extra endpoints or create entities for them. Ignore incidental fields returned in shared responses |

Cache records at a slower interval (the integration currently uses 10 minutes),
usage at 15 minutes, firmware at six hours, and capabilities at 24 hours.
Refresh affected configuration after a command and after rediscovery. Read
optional schedules/history/diagnostics on demand or when that feature is
actually enabled. These are cloud fetch rules, not local/cloud routing rules.

## Recommended reconciliation of cloud writes

**Choose one route per semantic command. Do not broadcast every write to S,
R, and L, and do not copy discrepant readings back into the other APIs.**
Most differences reflect lag or representation, not three stores to repair.

| Command | Preferred route | Alternative / verification |
| --- | --- | --- |
| Setpoint / mode; supported AC controls | S | No verified R/L replacement. Confirm device-reported values with the read policy; REC `Mode` is not a fallback |
| Offset | S | No verified alternative; confirm ST |
| Lock / brightness / setpoint limits | S | R settings are a validated alternative; L is the same legacy path on another host. Select an alternative for a known route/device limitation, not as routine fan-out. Confirm ST or equivalent LT; a REC echo alone is insufficient |
| Screen units | R settings | L if that route is unavailable; S is also supported. Read REC for configured units and ST for reported confirmation when it catches up |
| Heater profile | R settings | L as host fallback; S is also supported. Read REC and ST, preserving a pending/mismatched state if application is unconfirmed |
| Existing device schedule creation | S, only when explicitly using Mysa scheduling | Keep an existing legacy schedule in its native format unless deliberately migrating it. Avoid creating parallel S/v1/v2 schedules for the same intent. Full-body in-place editing is outside client scope |
| Schedule removal / hold / resume | Remove through the resource's native route; S for holds | Re-read that schedule/hold source. Removing a template is not removing its applied schedules; a hold record alone does not confirm an immediate target change |
| Firmware update / temporary live reporting | S dedicated endpoint | Confirm firmware availability/version or advancing telemetry, respectively; no R/L duplicate trigger |
| Claim / remove / replace | Existing explicit setup/recovery method | Use the documented operation and host; never run these as automatic write-repair strategies |
| Time zone | R, only for an explicit Mysa schedule configuration request | Verify REC; changing HA's time zone must not silently rewrite the thermostat |
| **Skip** operations, cloud renaming, app-only settings | No new write path | Use HA's corresponding features; no background synchronization into the Mysa app |

Validate capabilities and ranges, serialize changes per device, and send the
smallest semantic change. Send both brightness values together; preserve a
valid minimum/maximum pair. A setpoint change and a mode change are distinct
commands unless deliberately combined in one S body. Use the mode-specific
AC fan conversion in `commands.py`; raw enum values are not interchangeable.

Record an accepted command as **pending**, then re-read its confirmation
source. Matching `desired` or echoed REC is not physical confirmation. If the
requested value already matched the previous confirmed state, treat it as an
idempotent no-op, not a liveness test. A changed device report can confirm
application; where only REC is available, report stored configuration without
claiming device confirmation.

An explicit unsupported-route response can justify a validated alternative.
After a timeout or 200 without confirmation, re-read first: the command may
still apply. Do not immediately resend through another API, oscillate settings
to force synchronization, or apply a late retry after a newer user command.
Use bounded retries/backoff. Resource creation, firmware triggers, and other
non-idempotent actions require checking their outcome before any retry.

## Other protocol and implementation details

### Important wire rules

| Area | Rule |
| --- | --- |
| Time / mode | Legacy field timestamps can mix seconds and milliseconds in one response. S `modes.mode`, TEL `mode`, and LT mode fields have different enums; Lite heat is S mode 4 but TEL mode 3 |
| Telemetry quality | One Lite returned implausible future TEL timestamps while LT returned current readings. Freshness must be per field/source, not inferred from HTTP success |
| Electrical data | Lite current/power fields were zero and voltage was configured; legacy device energy summaries also produced implausible averages. Neither establishes measured consumption |
| Usage | `/telemetry/usage/{dev}` uses `timezone`, Unix-second `start`/`end`, `summary`, and `aggregatedReadings`; raw readings need the expanded request described in the [usage reference](../../../../docs/cloud-state.md#usage) |
| Legacy batch history | `/batchReadings/v2/{dev}` takes `attributes`, `start`, `end`, `attributesProcesses`; relevant fields include `h`, `c`, `s`, `a.oC`, `a.oH`, `ms`, `fs`, `tm`, `t`. Use `sum` for runtime counters, `none` for raw values; normalize timestamp-keyed buckets before use |
| Modern schedules | Events use Sunday = `"0"`, local `HH:MM:00`, `clientKey`, and semantic `state`. Use the device's `TimeZone`. In-place PUT needs `name`, `events`, `deviceType`, `homeId`, `deviceIds`, `reusable`; a name/events-only body fails |
| Legacy v1 schedules | POST body uses `ScheduledActions`, mapping weekday names to `{Minute,SetPoint}` entries. R additionally requires `Device` and `Model`. Seven empty weekday arrays clear events |
| Legacy v2 schedules | POST body: `Schedule` with `Version`, `Owner`, `Events` (plus `Id` for update), `Devices`, and `Model`. GET uses the **device ID**; DELETE uses the **schedule ID** with `Devices` and `Model` in its body |
| Schedule discovery | Modern lists can omit legacy schedules; REC `Schedule` can retain a deleted ID. Inspect the actual legacy resources. Saved-template apply creates separate `V2Schedules` resources |
| Holds | `startTime` must be in the future; the client adds ten seconds. Temporary holds add `endTime`. Both are UTC ISO timestamps. Do not infer an immediate setpoint change merely from hold creation |
| Firmware | POST the trigger with JSON `{}`; a bodyless request returned 415. Trigger acceptance is not completed installation. No newer release was available during validation |
| Newer-model features | Lite rejected Smart Early On eligibility and had no newer OTA fleet. Remote tracking was not reported after a direct write. Gate these features by model; accepting `desired` is not support |

For legacy v2 schedules, `Events` maps weekday number → minute number → device
command. A Lite example is `{"0":{"1437":{"BB2L":"3|22|%|%|%|%|%"}}}`.
The pipe-separated command has its own legacy enum/field ordering; do not
insert S's mode number. Detailed other-model and provisioning notes remain in
the [state and model reference](../../../../docs/cloud-state.md) and
[account/schedule workflows](../../../../docs/cloud-workflows.md). This guide
owns the reconciled protocol conclusions and selection recommendations.

### Client status

The [Cloud implementation status and plan](../../../../docs/implementation/cloud.md)
is the single work list, reviewed against code on October 12, 2026. Existing
endpoint methods and semantic encoders cover S control and R settings, with
AC mappings supported by fixtures rather than hardware validation. Dual acquisition, per-field observations, pending commands, verified settings
route alternatives, native legacy schedule cleanup/recovery and source diagnostics
are implemented with regression coverage. Firmware triggering sends JSON `{}`.
Device application and installation of an offered release remain separate hardware
validation work; see [completion tests](../../../../tests/test_cloud_completion.py).

### Using the client

`MysaAuth` and `MysaClient` are exported by [`__init__.py`](__init__.py).
The caller supplies/owns the `aiohttp` session; use HA's shared session inside
the integration. For example:

```python
from custom_components.mysa_local.protocols.cloud import MysaAuth, MysaClient, Mode


async def request_heat_target(
    session, refresh_token: str, user_agent: str, device_id: str
):
    auth = MysaAuth(session, refresh_token=refresh_token, user_agent=user_agent)
    client = MysaClient(session, auth, user_agent=user_agent)
    await client.set_temperature(device_id, Mode.HEAT, 21.5)
    # Request accepted; subsequent reads still need to confirm application.
```

### How it is organized

| File | Responsibility |
| --- | --- |
| [`auth.py`](auth.py), [`srp.py`](srp.py) | Cognito authentication, serialized token refresh, SRP calculations |
| [`client.py`](client.py) | HTTP requests, endpoint methods, response handling |
| [`commands.py`](commands.py) | Semantic controls, enum conversion, schedule encoding |
| [`models.py`](models.py) | Typed response parsing, preserving original JSON in `raw` |
| [`const.py`](const.py), [`exceptions.py`](exceptions.py) | Hosts, protocol constants/enums, errors |

The package uses the standard library and `aiohttp`. It is bundled with the
integration, not separately distributed; importing through the integration
package still initializes Home Assistant code. Polling/confirmation policy
belongs to [`cloud_coordinator.py`](../../cloud_coordinator.py) and
[`cloud_state.py`](../../cloud_state.py), not the HTTP
transport. This document's proposed cloud arbitration should preserve that
separation.

### Errors, retries, and integration boundary

The current transport uses a 20-second timeout. A 401 causes one forced token
refresh and retry; a remaining 401 or 403 raises `MysaApiError`, not automatic
reauthentication. Route absence or permissions can explain 403. A 404 raises
`MysaNotFoundError`; only resource-specific helpers such as schedule/hold reads
normalize an absent resource to an empty result. A 429 raises
`MysaRateLimitError` with numeric `Retry-After` when supplied; timeout,
transport failure, or 5xx raises `MysaConnectionError`. There is no general
transport retry loop.

Cognito failures use the `MysaAuthError` hierarchy. The implementation handles
the password-verifier SRP challenge; unsupported challenges raise
`MysaUnsupportedChallengeError`.

### Validation references

HTTP/parser behavior is covered by [`tests/api/test_client.py`](../../../../tests/api/test_client.py)
and SRP by [`tests/api/test_srp.py`](../../../../tests/api/test_srp.py), using
fixtures rather than live devices. Live checks established the distinctions
between accepted writes, stored records, and reported/applied values above;
full scheduled execution, notification delivery, and installation of a new
firmware release were not established. The two Lites also differed in cloud
setpoint/mode application, so confirmation remains necessary even for a
supported route.
