# Cloud implementation status and plan

Last reviewed: **2026-10-12**, code commit `dc3fcfe`. Status reflects the existing
client, models, command encoders, coordinator, and schedule/account callers.

The [cloud protocol reference](../../custom_components/mysa_local/protocols/cloud/README.md)
owns S/R/L definitions, endpoint evidence and recommended selection rules.
S means structured services; R means legacy REST through the modern host;
L means legacy REST through the older host. L is an operation-specific fallback,
not a third continuous poll or broadcast write destination.

An **Implemented** row covers only its named operation. Generic `update_state()`
and `update_settings()` do not implement every accepted field. Working encoders
still depend on the shared confirmation and acquisition work below. AC mappings
have code/fixture support, not AC hardware validation. A rejected or nonexistent
route is protocol evidence, not automatically missing client functionality.

- [Implemented coverage](#implemented-coverage)
- [Needs adjustment](#needs-adjustment)
- [Planned work](#planned-work)
- [Out of scope](#out-of-scope)
- [Completion criteria and dependencies](#completion-criteria-and-dependencies)

Statuses: Implemented, Needs adjustment, Planned, Deferred, Out of scope.
Planned stages are Next, Follow-up, and Model-gated; the last two are conditional,
not commitments to ship. Blocked is a dependency annotation, not an API verdict.

## Implemented coverage

| ID / status | Operation and route | Scope / next action | Evidence |
| --- | --- | --- | --- |
| <a id="c-i01"></a>**C-I01** · Implemented | Authentication and HTTP transport · S, R, L | `MysaAuth`, `MysaClient._request()`: shared raw ID-token authentication, refresh/retry on 401, typed errors, request timeout; explicit L requests already work | [Protocol](../../custom_components/mysa_local/protocols/cloud/README.md#operations-and-relevance-to-home-assistant); [code](../../custom_components/mysa_local/protocols/cloud/client.py); [API tests](../../tests/api/test_client.py) |
| <a id="c-i02"></a>**C-I02** · Implemented | Account/home/device bootstrap · S | `bootstrap()` and `Bootstrap`; supplies account IDs, homes, and initial device records | [Protocol](../../custom_components/mysa_local/protocols/cloud/README.md#operations-and-relevance-to-home-assistant); [code](../../custom_components/mysa_local/protocols/cloud/client.py); [API tests](../../tests/api/test_client.py) |
| <a id="c-i03"></a>**C-I03** · Implemented | Device records and model/remote metadata · R | `get_devices()`, `DeviceRecord`; names, time zones, identity, stored settings, AC command availability | [Protocol](../../custom_components/mysa_local/protocols/cloud/README.md#operations-and-relevance-to-home-assistant); [code](../../custom_components/mysa_local/protocols/cloud/client.py); [API tests](../../tests/api/test_client.py) |
| <a id="c-i04"></a>**C-I04** · Implemented | Device capabilities · S | `get_capabilities()`, `Capabilities`; supported controls and ranges | [Protocol](../../custom_components/mysa_local/protocols/cloud/README.md#operations-and-relevance-to-home-assistant); [code](../../custom_components/mysa_local/protocols/cloud/models.py); [API tests](../../tests/api/test_client.py) |
| <a id="c-i05"></a>**C-I05** · Implemented | State write encoding · S | `update_state()`, `set_temperature()`, `set_mode()`; the latter two need shared confirmation work [C-A04](#c-a04) / [C-A05](#c-a05), not an invented legacy mode/setpoint command | [Protocol](../../custom_components/mysa_local/protocols/cloud/README.md#operations-and-relevance-to-home-assistant); [code](../../custom_components/mysa_local/protocols/cloud/commands.py); [API tests](../../tests/api/test_client.py) |
| <a id="c-i06"></a>**C-I06** · Implemented | Button lock, paired brightness, limits, offset · S | `set_button_lock()`, `set_display_brightness()`, `set_setpoint_limit()`, `set_temperature_offset()`; semantic field encoders exist | [Protocol](../../custom_components/mysa_local/protocols/cloud/README.md#operations-and-relevance-to-home-assistant); [code](../../custom_components/mysa_local/protocols/cloud/commands.py); [API tests](../../tests/api/test_client.py) |
| <a id="c-i07"></a>**C-I07** · Implemented | AC fan and directional swing · S | `set_fan_speed()`, `set_swing()`; model-specific fan conversion and supported-command filtering exist | [Protocol](../../custom_components/mysa_local/protocols/cloud/README.md#operations-and-relevance-to-home-assistant); [code](../../custom_components/mysa_local/protocols/cloud/commands.py); [API tests](../../tests/api/test_client.py) |
| <a id="c-i08"></a>**C-I08** · Implemented | Stored settings, screen units, heater profile · R | `update_settings()`, `set_display_unit()`, `set_heater_type()`; keep R as the default for the two named convenience methods | [Protocol](../../custom_components/mysa_local/protocols/cloud/README.md#operations-and-relevance-to-home-assistant); [code](../../custom_components/mysa_local/protocols/cloud/commands.py); [API tests](../../tests/api/test_client.py) |
| <a id="c-i09"></a>**C-I09** · Implemented | Firmware availability and installed/offered versions · R | `get_firmware_info()`, `FirmwareInfo`; update triggering is [C-A01](#c-a01) | [Protocol](../../custom_components/mysa_local/protocols/cloud/README.md#operations-and-relevance-to-home-assistant); [code](../../custom_components/mysa_local/protocols/cloud/client.py); [API tests](../../tests/api/test_client.py) |
| <a id="c-i10"></a>**C-I10** · Implemented | Runtime summaries and temperature buckets · S | `get_usage()`, `UsageDay`, `UsageBucket`; existing `oH1` / `t` summary scope is useful and should remain | [Protocol](../../custom_components/mysa_local/protocols/cloud/README.md#operations-and-relevance-to-home-assistant); [code](../../custom_components/mysa_local/protocols/cloud/client.py); [API tests](../../tests/api/test_client.py) |
| <a id="c-i11"></a>**C-I11** · Implemented | State-change history · S | `get_state_history()`, `StateChange`; bounded reads with change-source information | [Protocol](../../custom_components/mysa_local/protocols/cloud/README.md#operations-and-relevance-to-home-assistant); [code](../../custom_components/mysa_local/protocols/cloud/client.py); [API tests](../../tests/api/test_client.py) |
| <a id="c-i12"></a>**C-I12** · Implemented | Modern schedule creation and detachment · S | `create_schedule()`, `delete_schedule()`, schedule event encoding; preserve metadata and attachments. Complete discovery is separate work below; in-place editing is excluded ([C-P07](#c-p07)) | [Protocol](../../custom_components/mysa_local/protocols/cloud/README.md#operations-and-relevance-to-home-assistant); [code](../../custom_components/mysa_local/protocols/cloud/client.py); [API tests](../../tests/api/test_client.py) |
| <a id="c-i13"></a>**C-I13** · Implemented | Hold read/create and resume · S | `get_hold()`, `set_hold()`, `resume_schedule()`; permanent/temporary records and absent-hold handling. Hold storage is not an immediate-setpoint confirmation | [Protocol](../../custom_components/mysa_local/protocols/cloud/README.md#operations-and-relevance-to-home-assistant); [code](../../custom_components/mysa_local/protocols/cloud/client.py); [API tests](../../tests/api/test_client.py) |
| <a id="c-i14"></a>**C-I14** · Implemented | Shortcuts for account maintenance · R | `get_shortcuts()`, `update_shortcut()`; retain inspection and action-map cleanup without building a Mysa scenes UI | [Protocol](../../custom_components/mysa_local/protocols/cloud/README.md#operations-and-relevance-to-home-assistant); [code](../../custom_components/mysa_local/protocols/cloud/client.py); [API tests](../../tests/api/test_client.py) |
| <a id="c-i15"></a>**C-I15** · Implemented | Claim and remove supported devices · R | `add_device()`, `delete_device()`; explicit setup/recovery, model checks, claim conflict handling, settings-preserving option | [Protocol](../../custom_components/mysa_local/protocols/cloud/README.md#operations-and-relevance-to-home-assistant); [code](../../custom_components/mysa_local/protocols/cloud/client.py); [API tests](../../tests/api/test_client.py) |
| <a id="c-i16"></a>**C-I16** · Implemented | Replace an existing device record · L | `replace_device()` via `/devices/purgeadd`; explicit recovery operation, not automatic repair | [Protocol](../../custom_components/mysa_local/protocols/cloud/README.md#operations-and-relevance-to-home-assistant); [code](../../custom_components/mysa_local/protocols/cloud/client.py); [API tests](../../tests/api/test_client.py) |
| <a id="c-i17"></a>**C-I17** · Implemented | Request temporary live reporting · S | `start_live_mode()`; initiation and returned device IDs. Advancing telemetry, not acceptance alone, demonstrates reporting | [Protocol](../../custom_components/mysa_local/protocols/cloud/README.md#operations-and-relevance-to-home-assistant); [code](../../custom_components/mysa_local/protocols/cloud/client.py); [API tests](../../tests/api/test_client.py) |
| <a id="c-i18"></a>**C-I18** · Implemented | Existing diagnostic/status fields · S | `DeviceState.rssi`, `in_demand_response`, identity/firmware and connection fields already parse; freshness/provenance improvements belong below | [Protocol](../../custom_components/mysa_local/protocols/cloud/README.md#recommended-reconciliation-of-cloud-reads); [code](../../custom_components/mysa_local/protocols/cloud/client.py); [API tests](../../tests/api/test_client.py) |

## Needs adjustment

| ID / status | Operation and route | Scope / next action | Evidence |
| --- | --- | --- | --- |
| <a id="c-a01"></a>**C-A01** · Needs adjustment | Firmware update trigger · S | `start_firmware_update()` sends no body; live request returned 415 **Adjustment:** Send JSON `{}`. Retain explicit invocation and verify subsequent firmware status; do not duplicate the trigger on L [Done when / dependencies](#c-a01-criteria). | [Protocol](../../custom_components/mysa_local/protocols/cloud/README.md#recommended-reconciliation-of-cloud-writes); [code](../../custom_components/mysa_local/protocols/cloud/client.py); [API tests](../../tests/api/test_client.py) |
| <a id="c-a02"></a>**C-A02** · Needs adjustment | Structured state parsing · S | `get_state()` / `get_states()` return `DeviceState` with one reading timestamp and no typed per-section provenance; numeric/time helpers do not cover the proposed normalization rules **Adjustment:** Preserve relevant timestamps, distinguish measurements from configuration, reject invalid/non-finite values, and handle bad/out-of-range timestamps without losing the device. Keep `desired` out of confirmed state [Done when / dependencies](#c-a02-criteria). | [Protocol](../../custom_components/mysa_local/protocols/cloud/README.md#recommended-reconciliation-of-cloud-reads); [code](../../custom_components/mysa_local/protocols/cloud/models.py); [API tests](../../tests/api/test_client.py) |
| <a id="c-a03"></a>**C-A03** · Needs adjustment | State fetch and batch failure handling · S, then S + R/L | `get_states()` omits failed entries; coordinator `_async_fetch()` replaces the entire state dictionary with that result **Adjustment:** Preserve previous observations with their original age for missing devices/fields. Expose partial errors and let successful sources update independently [Done when / dependencies](#c-a03-criteria). | [Protocol](../../custom_components/mysa_local/protocols/cloud/README.md#recommended-reconciliation-of-cloud-reads); [code](../../custom_components/mysa_local/cloud_coordinator.py); [API tests](../../tests/api/test_client.py) |
| <a id="c-a04"></a>**C-A04** · Needs adjustment | Accepted writes versus confirmed state · S, R; later L alternatives | `_async_write_state()` copies accepted values into cached **`reported`** as well as `desired`; `_async_write_settings()` optimistically changes REC **Adjustment:** Store pending commands separately from real responses. A read must confirm application or stored configuration as appropriate; do not mislabel an optimistic cache as device evidence [Done when / dependencies](#c-a04-criteria). | [Protocol](../../custom_components/mysa_local/protocols/cloud/README.md#recommended-reconciliation-of-cloud-writes); [code](../../custom_components/mysa_local/cloud_coordinator.py); [API tests](../../tests/api/test_client.py) |
| <a id="c-a05"></a>**C-A05** · Needs adjustment | Confirmation and retries · S, R; later L alternatives | Writes are already spaced and followed by a refresh, but lack the proposed per-operation confirmation status/source selection **Adjustment:** Reconcile against the proper source, preserve newer commands, and bound retries. A timeout or 200 without confirmation must not immediately trigger a second API write. Diagnose the Lite setpoint/mode discrepancy without treating REC `Mode` as control [Done when / dependencies](#c-a05-criteria). | [Protocol](../../custom_components/mysa_local/protocols/cloud/README.md#recommended-reconciliation-of-cloud-writes); [code](../../custom_components/mysa_local/cloud_coordinator.py); [API tests](../../tests/api/test_client.py) |
| <a id="c-a06"></a>**C-A06** · Needs adjustment | Complete schedule discovery and cleanup · S + R/L | `get_schedules()` reads only the modern list, which can omit legacy schedules; callers use it for inspection/cleanup **Adjustment:** Retain the modern read, add the native legacy reads below, and carry schedule origin/attachment IDs into cleanup. Do not assume an empty S list or stale REC pointer describes all schedules [Done when / dependencies](#c-a06-criteria). | [Protocol](../../custom_components/mysa_local/protocols/cloud/README.md#recommended-reconciliation-of-cloud-writes); [code](../../custom_components/mysa_local/protocols/cloud/client.py); [API tests](../../tests/api/test_client.py) |
| <a id="c-a07"></a>**C-A07** · Needs adjustment | Existing cloud health and diagnostics · S + R/L | Current freshness guards and connection diagnostics describe one cloud state/poll result **Adjustment:** Extend them to distinguish request success, observation age, selected field source, and each active API's health. Preserve the existing future-timestamp guard; one route failure must not imply all cloud sources failed [Done when / dependencies](#c-a07-criteria). | [Protocol](../../custom_components/mysa_local/protocols/cloud/README.md#recommended-reconciliation-of-cloud-reads); [code](../../custom_components/mysa_local/cloud_coordinator.py); [API tests](../../tests/api/test_client.py) |

<a id="c-a08"></a>
**C-A08 · Needs adjustment — malformed schedule discovery.**
`get_schedules()` treats a non-list result as an empty list and filters non-object
entries, potentially hiding an incomplete discovery before replacement. Fail
without mutation on unexpected shapes. See [client](../../custom_components/mysa_local/protocols/cloud/client.py),
[schedule caller](../../custom_components/mysa_local/schedule.py), and
[criteria](#c-a08-criteria). This gap is established by source review, not a live response.

## Planned work

| ID / status | Operation and route | Scope / next action | Evidence |
| --- | --- | --- | --- |
| <a id="c-p01"></a>**C-P01** · Planned | Legacy live-state read and parser · R, L fallback | **Next.** Add `GET /devices/state`; parse per-field value/time, mixed timestamp units, temperature, humidity, target, mode, lock, duty and diagnostics. Normalize enums without reusing S's raw parser [Done when / dependencies](#c-p01-criteria). | [Protocol](../../custom_components/mysa_local/protocols/cloud/README.md#recommended-reconciliation-of-cloud-reads); [code](../../custom_components/mysa_local/protocols/cloud/models.py); [API tests](../../tests/api/test_client.py) |
| <a id="c-p02"></a>**C-P02** · Planned | Dual acquisition and per-field read selection · S + R, or L replacing R | **Next.** Poll S batch and legacy state independently; apply the README's field policies, retain cached observation age, reject future data, and preserve source metadata [Done when / dependencies](#c-p02-criteria). | [Protocol](../../custom_components/mysa_local/protocols/cloud/README.md#recommended-reconciliation-of-cloud-reads); [code](../../custom_components/mysa_local/protocols/cloud/client.py); [API tests](../../tests/api/test_client.py) |
| <a id="c-p03"></a>**C-P03** · Planned | Operation-specific host routing · R → L | **Next.** Add an explicit allowlist for validated fallback reads/settings operations, with backoff. Keep S on its supported host; do not globally switch the client's base URL [Done when / dependencies](#c-p03-criteria). | [Protocol](../../custom_components/mysa_local/protocols/cloud/README.md#recommended-reconciliation-of-cloud-writes); [code](../../custom_components/mysa_local/protocols/cloud/client.py); [API tests](../../tests/api/test_client.py) |
| <a id="c-p04"></a>**C-P04** · Planned | Semantic alternatives for existing device settings · R/L lock, brightness, limits; S screen units/profile | **Next.** Expose the verified encoders through deliberate route selection. Raw writes are already possible; typed alternative selection is missing. Use one selected route and appropriate confirmation [Done when / dependencies](#c-p04-criteria). | [Protocol](../../custom_components/mysa_local/protocols/cloud/README.md#recommended-reconciliation-of-cloud-writes); [code](../../custom_components/mysa_local/protocols/cloud/client.py); [API tests](../../tests/api/test_client.py) |
| <a id="c-p05"></a>**C-P05** · Planned | Legacy v1 schedule inspection/clearing · R, L fallback | **Next.** Read `/schedules`; clear only the selected device's events with `/schedules/device/update/{dev}`. Include required R body fields. Do not add a parallel legacy schedule editor by default [Done when / dependencies](#c-p05-criteria). | [Protocol](../../custom_components/mysa_local/protocols/cloud/README.md#recommended-reconciliation-of-cloud-writes); [code](../../custom_components/mysa_local/protocols/cloud/client.py); [API tests](../../tests/api/test_client.py) |
| <a id="c-p06"></a>**C-P06** · Planned | Legacy v2 schedule inspection/removal · R, L fallback | **Next.** Read `/schedules/v2/{dev}` by **device ID**; delete by **schedule ID** with the required body. Include applied saved-template schedules without implementing template management [Done when / dependencies](#c-p06-criteria). | [Protocol](../../custom_components/mysa_local/protocols/cloud/README.md#recommended-reconciliation-of-cloud-writes); [code](../../custom_components/mysa_local/protocols/cloud/client.py); [API tests](../../tests/api/test_client.py) |
| <a id="c-p08"></a>**C-P08** · Planned | Expanded usage/raw-reading query · S | **Follow-up.** Extend the current summary-only query with bounded optional attributes/raw readings and corresponding parsed results; retain the simple runtime method [Done when / dependencies](#c-p08-criteria). | [Protocol](../../custom_components/mysa_local/protocols/cloud/README.md#recommended-reconciliation-of-cloud-writes); [code](../../custom_components/mysa_local/protocols/cloud/client.py); [API tests](../../tests/api/test_client.py) |
| <a id="c-p09"></a>**C-P09** · Planned | Legacy measurement history · R, L fallback | **Follow-up.** Add `/batchReadings/v2/{dev}` for explicit history fallback/diagnostics; normalize bucket timestamps and units, avoid overlap/double counting [Done when / dependencies](#c-p09-criteria). | [Protocol](../../custom_components/mysa_local/protocols/cloud/README.md#recommended-reconciliation-of-cloud-writes); [code](../../custom_components/mysa_local/protocols/cloud/client.py); [API tests](../../tests/api/test_client.py) |
| <a id="c-p10"></a>**C-P10** · Planned | Legacy setpoint-change history · R, L fallback | **Follow-up.** Add `/energy/setpoints/device/{dev}` for on-demand troubleshooting when current history is incomplete [Done when / dependencies](#c-p10-criteria). | [Protocol](../../custom_components/mysa_local/protocols/cloud/README.md#recommended-reconciliation-of-cloud-writes); [code](../../custom_components/mysa_local/protocols/cloud/client.py); [API tests](../../tests/api/test_client.py) |
| <a id="c-p11"></a>**C-P11** · Planned | Dedicated demand-response status fallback · R, L fallback | **Follow-up.** Add `/devices/{dev}/drstate` only when S does not supply the useful status; read-only, no enrollment management [Done when / dependencies](#c-p11-criteria). | [Protocol](../../custom_components/mysa_local/protocols/cloud/README.md#recommended-reconciliation-of-cloud-writes); [code](../../custom_components/mysa_local/protocols/cloud/client.py); [API tests](../../tests/api/test_client.py) |
| <a id="c-p12"></a>**C-P12** · Planned | Additional optional diagnostics · S, R/L | **Follow-up.** Parse internal temperatures/free heap and attach source/age; reuse state responses, keep entities disabled by default [Done when / dependencies](#c-p12-criteria). | [Protocol](../../custom_components/mysa_local/protocols/cloud/README.md#recommended-reconciliation-of-cloud-reads); [code](../../custom_components/mysa_local/protocols/cloud/client.py); [API tests](../../tests/api/test_client.py) |
| <a id="c-p13"></a>**C-P13** · Planned | Model-specific physical controls and measured electrical data · S; verified REC fields where needed | **Model-gated.** Add typed floor/probe, adaptive-display, remote-tracking/HVAC and measured-power support only for applicable hardware after its mappings and behavior are validated. Generic state/settings writes alone do not establish support [Done when / dependencies](#c-p13-criteria). | [Protocol](../../custom_components/mysa_local/protocols/cloud/README.md#recommended-reconciliation-of-cloud-writes); [code](../../custom_components/mysa_local/protocols/cloud/client.py); [API tests](../../tests/api/test_client.py) |
| <a id="c-p14"></a>**C-P14** · Planned | Newer-model OTA and claim workflow · Current services (S/R) | **Model-gated.** Support only as part of validated setup/update support for the relevant hardware, including its credential/fleet/finalization requirements; do not probe or enroll Lites into a fleet [Done when / dependencies](#c-p14-criteria). | [Protocol](../../custom_components/mysa_local/protocols/cloud/README.md#recommended-reconciliation-of-cloud-writes); [code](../../custom_components/mysa_local/protocols/cloud/client.py); [API tests](../../tests/api/test_client.py) |

## Out of scope

| ID / status | Operation and route | Scope / next action | Evidence |
| --- | --- | --- | --- |
| <a id="c-o01"></a>**C-O01** · Out of scope | Saved-schedule template create/edit/apply/delete · R/L `/homes/...savedSchedule...` | HA scheduling is more capable. Retain inspection/removal of the resulting **device schedules**, not a second template system | [Protocol](../../custom_components/mysa_local/protocols/cloud/README.md#operations-and-relevance-to-home-assistant); [code](../../custom_components/mysa_local/protocols/cloud/client.py) |
| <a id="c-o02"></a>**C-O02** · Out of scope | Legacy schedule creation/editing as another user-facing scheduler · R/L `/schedules...` | Do not maintain three schedule editors. Keep native legacy inspection/removal; use HA or the optional modern scheduler for new schedules | [Protocol](../../custom_components/mysa_local/protocols/cloud/README.md#operations-and-relevance-to-home-assistant); [code](../../custom_components/mysa_local/protocols/cloud/client.py) |
| <a id="c-o03"></a>**C-O03** · Out of scope | Mysa shortcut creation/application · R/L `/scene...`, `/homes/shortcut/apply/...` | Use HA scenes/scripts. Existing shortcut read/edit remains for account maintenance | [Protocol](../../custom_components/mysa_local/protocols/cloud/README.md#operations-and-relevance-to-home-assistant); [code](../../custom_components/mysa_local/protocols/cloud/client.py) |
| <a id="c-o04"></a>**C-O04** · Out of scope | Mysa zone management · R/L `/zones...` | Use HA areas/groups | [Protocol](../../custom_components/mysa_local/protocols/cloud/README.md#operations-and-relevance-to-home-assistant); [code](../../custom_components/mysa_local/protocols/cloud/client.py) |
| <a id="c-o05"></a>**C-O05** · Out of scope | Legacy device/home/zone energy summaries · R/L `/energy/device/...`, `/energy/home/...`, `/energy/zone/...` | Lite summaries are unreliable; group energy in HA from validated sources. Keep runtime and targeted history operations above | [Protocol](../../custom_components/mysa_local/protocols/cloud/README.md#operations-and-relevance-to-home-assistant); [code](../../custom_components/mysa_local/protocols/cloud/client.py) |
| <a id="c-o06"></a>**C-O06** · Out of scope | Separate whole-home energy dashboard API · S `/energy/v3/...` | Redundant with HA aggregation; unavailable on the tested Lite account | [Protocol](../../custom_components/mysa_local/protocols/cloud/README.md#operations-and-relevance-to-home-assistant); [code](../../custom_components/mysa_local/protocols/cloud/client.py) |
| <a id="c-o07"></a>**C-O07** · Out of scope | Geofences and phone crossing events · R/L `/geofence...` | Use HA presence/automations | [Protocol](../../custom_components/mysa_local/protocols/cloud/README.md#operations-and-relevance-to-home-assistant); [code](../../custom_components/mysa_local/protocols/cloud/client.py) |
| <a id="c-o08"></a>**C-O08** · Out of scope | Telemetry alert rules and reminders · R/L `/devices/{dev}/telemetry_alerts`, `/homes/reminders/...` | Use HA automations and notifications | [Protocol](../../custom_components/mysa_local/protocols/cloud/README.md#operations-and-relevance-to-home-assistant); [code](../../custom_components/mysa_local/protocols/cloud/client.py) |
| <a id="c-o09"></a>**C-O09** · Out of scope | Push registration, notification delivery/preferences · R/L notification/push/preference routes | Use HA's notification services | [Protocol](../../custom_components/mysa_local/protocols/cloud/README.md#operations-and-relevance-to-home-assistant); [code](../../custom_components/mysa_local/protocols/cloud/client.py) |
| <a id="c-o10"></a>**C-O10** · Out of scope | Mysa profiles, homes, invitations, permissions, account deletion/migration · R/L account administration routes | HA only needs discovery and explicit device maintenance, not the Mysa account-management app | [Protocol](../../custom_components/mysa_local/protocols/cloud/README.md#operations-and-relevance-to-home-assistant); [code](../../custom_components/mysa_local/protocols/cloud/client.py) |
| <a id="c-o11"></a>**C-O11** · Out of scope | Dedicated cloud rename / app eco / animation APIs · R/L settings | HA naming/automation needs no app-state synchronization. Generic settings writes exist; do not add dedicated methods or claim unverified physical effects | [Protocol](../../custom_components/mysa_local/protocols/cloud/README.md#operations-and-relevance-to-home-assistant); [code](../../custom_components/mysa_local/protocols/cloud/client.py) |
| <a id="c-o12"></a>**C-O12** · Out of scope | Smart Early On and utility enrollment management · S/app services | Prefer HA scheduling; Early On is unavailable on Lite. Keep useful read-only demand-response status | [Protocol](../../custom_components/mysa_local/protocols/cloud/README.md#operations-and-relevance-to-home-assistant); [code](../../custom_components/mysa_local/protocols/cloud/client.py) |
| <a id="c-o13"></a>**C-O13** · Out of scope | App feature flags, phone diagnostics, communication preferences, localization, analytics · S/R/L app routes | Not thermostat functionality; phone diagnostic uploads do not probe the thermostat | [Protocol](../../custom_components/mysa_local/protocols/cloud/README.md#recommended-reconciliation-of-cloud-reads); [code](../../custom_components/mysa_local/protocols/cloud/client.py) |
| <a id="c-o14"></a>**C-O14** · Out of scope | Alexa/account integration and property-manager services · App-specific services, including outside S/R/L | HA supplies its own integrations; outside this client scope | [Protocol](../../custom_components/mysa_local/protocols/cloud/README.md#operations-and-relevance-to-home-assistant); [code](../../custom_components/mysa_local/protocols/cloud/client.py) |
| <a id="c-o15"></a>**C-O15** · Out of scope | Legacy NFC / anonymous AC setup helpers · L/app setup routes | No role in the authenticated HA control path | [Protocol](../../custom_components/mysa_local/protocols/cloud/README.md#operations-and-relevance-to-home-assistant); [code](../../custom_components/mysa_local/protocols/cloud/client.py) |
| <a id="c-o16"></a>**C-O16** · Out of scope | Duplicate discovery wrappers · R `/devices/capabilities`, `/users/readingsForUser`; R/L user/home/member/single-record/version lists | Existing bootstrap, device records, per-device capabilities, and firmware information cover current HA needs; add a specific endpoint only if a concrete missing field requires it | [Protocol](../../custom_components/mysa_local/protocols/cloud/README.md#operations-and-relevance-to-home-assistant); [code](../../custom_components/mysa_local/protocols/cloud/client.py) |
| <a id="c-o17"></a>**C-O17** · Out of scope | Lite measured-power/current entities or voltage writes for an energy dashboard · S/R/L returned fields and REC settings | Lite does not establish measured electrical consumption. Use an explicitly labeled estimate where appropriate, not extra cloud methods | [Protocol](../../custom_components/mysa_local/protocols/cloud/README.md#operations-and-relevance-to-home-assistant); [code](../../custom_components/mysa_local/protocols/cloud/client.py) |
| <a id="c-o18"></a>**C-O18** · Out of scope | Continuous third-host polling or write broadcasting · S + R + L | R/L overlap. Use per-field selection and one chosen write route; do not create duplicate traffic or commands | [Protocol](../../custom_components/mysa_local/protocols/cloud/README.md#operations-and-relevance-to-home-assistant); [code](../../custom_components/mysa_local/protocols/cloud/client.py) |
| <a id="c-p07"></a>**C-P07** · Out of scope | In-place modern schedule editing · S | Do not add full-body `PUT /schedule/{id}` as a client feature. Retain the wire format as protocol documentation only; use HA scheduling | [Protocol](../../custom_components/mysa_local/protocols/cloud/README.md#recommended-reconciliation-of-cloud-writes); [code](../../custom_components/mysa_local/protocols/cloud/client.py); [API tests](../../tests/api/test_client.py) |

## Completion criteria and dependencies

### C-A01 criteria

**Completion:** Encode an explicit empty JSON object and add a mocked request regression for it. Preserve one trigger, then status polling; demonstrate an available-release installation separately before claiming end-to-end validation.

**Dependencies:** None for encoding; offered firmware and separately authorized hardware work for installation.

### C-A02 criteria

**Completion:** Preserve section/field timestamps and source; reject non-finite readings, invalid/out-of-range time, and desired-only values without discarding an otherwise usable device. Add malformed/mixed-time fixture cases.

**Dependencies:** Coordinate normalized observation shape with [C-P01](#c-p01) and [C-P02](#c-p02).

### C-A03 criteria

**Completion:** Represent partial batch errors; retain missing device/field observations at their original age and allow successful independent sources to update. Test mixed success, missing entries, and full failure.

**Dependencies:** [C-A02](#c-a02), [C-P02](#c-p02).

### C-A04 criteria

**Completion:** Keep pending commands separate from real reported/configuration observations; accepted writes must not manufacture confirmed readings. Verify pending→confirmed/expired and overlapping commands with fakes.

**Dependencies:** [C-A02](#c-a02), [C-P02](#c-p02), [C-A05](#c-a05).

### C-A05 criteria

**Completion:** Bound operation-specific readback/retry, preserve newer commands, and prove an ambiguous response does not immediately cause a duplicate alternate-route write. Retain the differing Lite setpoint results as an unresolved hardware limit.

**Dependencies:** [C-P02](#c-p02), [C-P03](#c-p03); hardware diagnosis requires separate authorization.

### C-A06 criteria

**Completion:** Discover schedules across modern/v1/v2 resources with origin and attachment IDs; remove only selected-device resources and preserve shared schedules. Test absent S plus present legacy, stale REC pointers, and failures.

**Dependencies:** [C-P05](#c-p05), [C-P06](#c-p06), [C-A08](#c-a08).

### C-A07 criteria

**Completion:** Expose request health separately from observation age and selected source, retaining the future-time guard. One failing route must not invalidate successful independent sources.

**Dependencies:** [C-A02](#c-a02), [C-A03](#c-a03), [C-P02](#c-p02).

### C-A08 criteria

**Completion:** Reject malformed schedule lists and entries before destructive replacement/cleanup; a fixture regression must prove no follow-on mutation occurs.

**Dependencies:** Coordinate callers and recovery behavior with [C-A06](#c-a06).

### C-P01 criteria

**Completion:** Add explicit R legacy-state reads with normalized per-field timestamps, units/enums, and errors; test mixed seconds/milliseconds and absent/invalid values.

**Dependencies:** [C-A02](#c-a02); fallback uses [C-P03](#c-p03).

### C-P02 criteria

**Completion:** Acquire S and one legacy source independently; implement the governing per-field policy with provenance/age retention and no double counting. Test future timestamps, partial failure, and source disagreement.

**Dependencies:** [C-A02](#c-a02), [C-P01](#c-p01); [C-P03](#c-p03) for L fallback.

### C-P03 criteria

**Completion:** Use an allowlist of verified fallback operations with backoff; test that S stays on its host, R/L do not poll concurrently as duplicates, and writes are not broadcast.

**Dependencies:** [C-A05](#c-a05) for ambiguous-write outcomes.

### C-P04 criteria

**Completion:** Expose typed route selection for verified alternative settings with capability validation, one selected write, and appropriate reported/stored confirmation.

**Dependencies:** [C-P03](#c-p03), [C-A04](#c-a04), [C-A05](#c-a05).

### C-P05 criteria

**Completion:** Read v1, encode R required fields, and clear only the selected device with seven weekday arrays; test preservation of unrelated schedules.

**Dependencies:** [C-P03](#c-p03) for optional L fallback; feeds [C-A06](#c-a06).

### C-P06 criteria

**Completion:** Read v2 by device ID and delete by schedule ID with required body; retain origin/attachments including template-applied schedules. Test ID distinction and shared devices.

**Dependencies:** [C-P03](#c-p03) for optional L fallback; feeds [C-A06](#c-a06).

### C-P07 criteria

**Scope decision:** Out of scope. Do not implement full-body modern schedule editing
in the client. The protocol example documents server behavior only. No completion
work or dependencies remain for this item.

### C-P08 criteria

**Completion:** Add bounded optional attributes/raw readings and parsed results while preserving the existing summary method; test units and range limits.

**Dependencies:** Follow-up only; no default polling expansion.

### C-P09 criteria

**Completion:** Normalize legacy history buckets/units and use a bounded explicit fallback without overlapping-source accumulation.

**Dependencies:** [C-P03](#c-p03); explicit history use case.

### C-P10 criteria

**Completion:** Parse bounded legacy target-change history with source/time and errors, invoked only for troubleshooting.

**Dependencies:** [C-P03](#c-p03); explicit troubleshooting use case.

### C-P11 criteria

**Completion:** Use dedicated DR status only when useful S status is absent; test fallback and preserve read-only scope.

**Dependencies:** [C-P03](#c-p03); evidence that the extra read is needed.

### C-P12 criteria

**Completion:** Parse optional internal temperature/heap from already-fetched data with age/source; keep diagnostic entities disabled by default.

**Dependencies:** [C-A02](#c-a02), [C-P02](#c-p02); selected diagnostic use case.

### C-P13 criteria

**Completion:** Implement only applicable typed controls/measured values with representative model fixtures and separately authorized matching-hardware validation. Never infer Lite electrical measurement from configured voltage/zeros.

**Dependencies:** Matching hardware, recovered mappings, and selected model support.

### C-P14 criteria

**Completion:** Validate credential/fleet/claim/finalize and OTA lifecycle for matching hardware, including failure/cancel behavior; coordinate BLE ownership with the setup repository.

**Dependencies:** Matching hardware, BLE implementation owner, separate experiment authorization; no Lite fleet enrollment.


## Review and maintenance

The API tests establish request/model behavior at their named scope; they do not
close every pending criterion or prove physical behavior. Relevant integration
owners are the [cloud coordinator](../../custom_components/mysa_local/cloud_coordinator.py),
[schedules](../../custom_components/mysa_local/schedule.py),
[account cleanup](../../custom_components/mysa_local/account.py), and
[diagnostics](../../custom_components/mysa_local/diagnostics.py).
See the [architecture](../architecture.md) for local/cloud routing and persistence.

Update affected rows and evidence with implementation changes. Keep stable IDs
when moving completed work into coverage, and leave hardware limits explicit.
A probe script, accepted request, or generic update method does not close a
semantic item. Release history belongs in the changelog. Keep protocol payloads
in [examples](../examples/README.md) and selection algorithms in the primary
reference. No other protocol tracker is warranted by the current scoped backlog.
