# Cloud account and schedule workflows

The [cloud protocol reference](../custom_components/mysa_local/protocols/cloud/README.md) owns endpoint availability, S/R/L terminology, observed limitations, and recommended client policy. This page supplies detailed wire mechanics; implementation status belongs to the [cloud tracker](implementation/cloud.md).

The protocols were reverse-engineered from the original Mysa apps. Hardware observations cover Baseboard Lite `BB-V2-0-L`, firmware `3.17.5.13`. Other-model behavior remains app/code-derived unless explicitly stated.

- [Schedules, holds and shortcuts](#schedules-holds-and-shortcuts)
- [Account enrollment and removal](#adding-and-removing-devices)
- [Other account and app routes](#additional-endpoints)

## Schedules, holds and shortcuts

### Weekly schedules

`POST /schedule` creates and attaches a schedule. The response was 201 with `{"scheduleId":"..."}`.

[Full schedule creation exchange](examples/cloud/schedule-create.full-body.json)

Weekdays use Sunday = `"0"`; execution times use local `HH:MM:00`. Schedule times follow the device's `TimeZone`; the client does not convert from HA's time zone. `clientKey` identifies the submitting client.

Read schedules with `GET /schedule?homeId={home}&includeLegacyBaseboard=true&includeLegacyAc=true&includeLegacyInfloor=true`. It returns a list of objects with `id`, `name`, `reusable`, `homeId`, `deviceType`, `deviceIds`, `events`, `createdAt` and `updatedAt`. No schedule returned 404; Python converts that to an empty list.

The Lite's legacy schedule adapter normalizes responses: generated names become
`Schedule`, weekdays become integers, events are sorted Sunday-first, and
`clientKey` becomes `v2-migration`. Compare event behavior rather than requiring
the submitted and returned JSON to match exactly.

`DELETE /schedule/{scheduleId}/attach/{dev}` detaches the device. For an unshared
schedule, the response was "Schedule unattached from device and deleted".
In-place editing is verified through `PUT /schedule/{id}`; see the [full-body update example](examples/cloud/schedule-update.full-body.json). Send all six fields:
`name`, `events`, `deviceType`, `homeId`, `deviceIds`, and `reusable`. A body
containing only name/events fails. Acceptance and record readback did not
establish complete scheduled execution. This update operation is [outside client scope](implementation/cloud.md#c-p07);
its wire format is retained for reference. Mysa Local replaces schedules by detaching and recreating
them; these calls are non-atomic. Its [schedule implementation](../custom_components/mysa_local/schedule.py)
backs up the original and reconciles ambiguous failures before restoring it.

### Legacy schedules and saved templates

Read v1 with `GET /schedules`. To clear one device, POST
`/schedules/device/update/{dev}` with seven empty weekday arrays in
`ScheduledActions`; R also requires `Device` and `Model`. Preserve other devices.
Read v2 with `GET /schedules/v2/{dev}` using the device ID. Creation/update uses
`POST /schedules/v2/`: `Schedule` carries `Version`, `Owner`, `Events` (and `Id`
for update), with sibling `Devices` and `Model`. Preserve the native metadata;
do not derive `Model` or `Owner` from an event key. See the
[v2 update body](examples/cloud/schedule-v2-update.full-body.json),
[v2 delete body](examples/cloud/schedule-v2-delete.with-body.json), and
v1 clear variants for [R](examples/cloud/schedule-v1-clear.modern-host.json)
and [L](examples/cloud/schedule-v1-clear.legacy-host.json). Delete at
`/schedules/v2/{id}` using the schedule ID and a JSON body with `Devices` and
`Model`. A device ID and schedule ID are not interchangeable.

V2 `Events` maps weekday number → minute number → model command. The legacy
command fields are `mode|setpoint|fanSpeed|brightness|trackingSensor|lowerSetpoint|upperSetpoint`,
with `%` for unset fields. Mode is the legacy enum, not S's mode enum.
A Lite uses the model key `BB2L`.

Saved templates are read through `/homes/{home}/savedSchedule`; create at
`POST /homes/savedSchedule/create`, change name/events/apply using
`PUT /homes/savedSchedule/{id}/name`, `/events`, or `/apply`, and delete with
`DELETE /homes/savedSchedule/{id}`. Name, event and apply bodies respectively
carry `Name`, `Events`, and `Devices`. Applying creates separate `V2Schedules`
resources; deleting a template does not remove those device schedules. Event
editing worked on L but returned 404 on R. Do not infer schedule absence from
an empty modern list or an old REC `Schedule` pointer.

These mechanics support inspection and cleanup. Dedicated template management
and a second legacy schedule editor are [out of scope](implementation/cloud.md#out-of-scope).

### Holds

With a schedule active, a plain setpoint write held the new value until the next event. Explicit holds use `POST /schedule/hold`:

[Permanent hold exchange](examples/cloud/hold.permanent.json)

For a timed hold, use `"holdType":"temporary"` and add `"endTime":"<ISO UTC timestamp>"`. Both returned 201 with a `holdId`. Changing the hold kept the same ID.

The server rejects a start time that has already passed with "Start time must be greater than current time". Python starts the hold ten seconds in the future and formats timestamps with milliseconds and a `Z` suffix. HA also applies its state immediately through the normal local-first control path.

Mysa Local requires an active schedule for a hold. Its handling of a creation-time 404 as "hold needs schedule" is not hardware-verified.

`GET /schedule/hold/{dev}` reads the active hold; 404 means none. `DELETE /schedule/hold/{dev}` resumes the schedule. In the test, it returned "Device restored to follow its schedule" and restored the scheduled setpoint. Python treats a missing hold on resume as already complete.

### Home, Away and Vacation shortcuts

`GET /homes/{home}/scenes` returns `{"Scenes":[...]}`. Default shortcut IDs are `{home}_home`, `{home}_away` and `{home}_vacation`.

Edit a shortcut with `PATCH /scene/{shortcutId}`:

[Shortcut action replacement exchange](examples/cloud/shortcut.replace-actions.json)

The PATCH returned 202. Treat `DeviceActions` as the replacement action map. Use `{"DeviceActions":{},"AllDevices":true}` to clear the action map.

Apply with `POST /homes/shortcut/apply/{shortcutId}` and `{"Timestamp":<Unix seconds>}`; the response was `Ok`. Later shortcut-application results were inconsistent;
acceptance is not confirmation of the resulting thermostat state. The Python client implements reading and editing shortcuts for account cleanup. It has no apply wrapper.

## Adding and removing devices

For Wi-Fi provisioning and its relationship to local pairing and account enrollment, see the [Bluetooth reference](bluetooth.md#scope-and-compatibility). The sections below cover cloud account operations.

### Claiming an older model

This flow applies to Baseboard V1/V2/Lite, In-floor and AC. Account claims use
`POST /devices`; the device need not already be verified online. The examined mobile app
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

These cloud steps supplement the [Bluetooth credential-transfer flow](bluetooth.md#credential-transfer-on-newer-models).
This newer provisioning flow is untested on hardware here and not implemented by Mysa Local.

| Stage | Request or condition |
| --- | --- |
| Register in OTA fleet | `POST /ota/fleet/devices` with `{"deviceIds":["{dev}"],"fleetName":"<fleet>"}`; fleets are `bb3-prod`, `lv-prod` and `cs-prod` |
| Claim on account | `POST /devices` with base identity/home fields, without legacy heater defaults |
| Verify cloud connection | Wait up to five minutes for a newer `identity` timestamp from `GET /state/{dev}` |
| Finalize pairing | `POST /pairing/finalize` with `{"deviceId":"{dev}"}` |
| Obtain Matter setup code | `GET /matter/qr/{dev}` returns `{qrCode,pairingCode}` |

Registration follows credential transfer at the "register device" setup stage. Subsequent
firmware management uses the [newer OTA service](firmware-and-connectivity.md#newer-ota-service).

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
`/devices/capabilities`; their tested availability and host differences are recorded in the
[primary cloud reference](../custom_components/mysa_local/protocols/cloud/README.md#read-matrix).
Property-manager requests use `GET /zones` on `dash-api-v1.mysa.cloud`.
`/users/readingsForUser` is a defined but unverified route. Legacy NFC setup
uses `GET /nfc/auth-key`; it has no identified role in current provisioning.

## Retained app-only workflow mechanics

These are recovered legacy app request shapes, preserved for interpreting existing
account data. Use the primary reference for live R/L availability and the tracker
for scope decisions; this table does not select new HA features or claim delivery
of alerts, notifications, geofence effects, or invitations.

| Workflow | App request mechanics |
| --- | --- |
| Telemetry alerts | `GET/PUT/DELETE /devices/{dev}/telemetry_alerts`; PUT sends an alert object, DELETE identifies `ThresholdCondition` and `ThresholdMetric` |
| Zones | `POST /zones/` carries `devices` and `zone`; `POST /zones/{id}` changes `Name`; DELETE removes the zone |
| Geofences | `POST/PATCH/GET/DELETE /geofence/` with optional ID; crossings carry `Users`, `Phone`, `CrossDirection`; the app also has an unsecured background crossing path, not a generic control credential |
| Reminders | `GET/POST/PUT/DELETE /homes/reminders/` with the relevant ID; writes wrap the reminder in `payload` |
| Home records | `POST /homes`, `PUT /homes/{id}` for fields such as `Name` or `Address.PostalCode`, and `DELETE /homes/{id}` |
| User profile/preferences | `PATCH /users/{user}` for fields such as `LanguagePreference`, `PrimaryHome`, or `ERate`; notification settings use `GET/PUT /preferences/User_{user}_Home_{home}/Notifications` |
| Home access | `GET /homes/{home}/users`; `POST /homes/revokeUserHomeAccess/{user}` carries `Home`; `/users/{user}/updateUserDeviceAccess` carries `DeviceToRevoke`, `DeviceToGrantAccess`, `Home` |
| Invitations | `POST /invite/` carries `Owner`, `Home`, `Invitee`, `Devices`; lists use `/invite/users/{user}` and `/invite/homes/{home}`; `PUT /invite/{id}/` carries `Action` or `Devices` |
| Account deletion/migration | `POST /users/{user}/deleteUser` carries `userSub`, `reason`, `email`, `forgotten`; legacy `POST /homes/migrate` carries `From`, `To`, `Devices` |
| Push/notifications | `/users/pushNotification` registers phone tokens; `/notifications` accepts notification keys; neither establishes delivery to the user |
| Legacy aggregate energy | `POST /energy/device/{dev}`, `/energy/home/{home}`, `/energy/zone/{id}` use `Id`, `Scope`, `Res`, `Timestamp`, `TimeZone`, `PhoneTimezone`; Scope is Day/Week/Month and Res is halfHour/oneHour/Day |
| AC anonymous setup | `POST /users/unauth` with `clientId` obtains temporary credentials in the recovered AC IR setup flow; its current permissions are unknown and it is not an established normal-control alternative |

Older shortcut applications can use `/scene/apply/{id}` behind an app flag;
current recovered clients use `/homes/shortcut/apply/{id}`. Do not assume every
route declared in a bundle has a live caller: `/users/readingsForUser` was defined
without an identified old-app call site. Property-manager `GET /zones` on its
separate host and Alexa authorization are app services outside this client.
