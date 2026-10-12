# Changelog

## 0.6.0 (2026-10-12)

- Use the optional `mysa-client` MQTT transport for live, read-only telemetry
  when Mysa account access and cloud use are enabled. The new Live MQTT
  readings option is on by default; one session serves the configured account
  thermostats. HomeKit and HTTPS command routing are unchanged.
- Prefer MQTT humidity and numeric heating duty cycle while readings are no
  more than 30 seconds old by local receipt time. Fall back per field to valid
  HTTPS telemetry; use a fresh MQTT duty-cycle fraction for estimated heater
  power before the existing fallback behavior. MQTT connection failures do not
  block startup or local control. Expose a numeric Heating duty cycle sensor
  when the device reports that measurement.
- Add a default-on per-thermostat **Use live MQTT readings** setting under
  thermostat settings. The integration-level option remains the master switch;
  either setting can exclude a device from the shared MQTT session.

Validated with 287 Home Assistant tests and 177 client tests on Python 3.11
and 3.14; lint, type checks, documentation checks, and distribution build pass.
Initially deployed with `mysa-client` 0.1.2 on Home Assistant 2026.10.1. Both
installed Baseboard Lites reported MQTT humidity and duty cycle, adding two duty
cycle sensors (42 to 44 active entities). Saved configuration, sign-in,
pairings, existing entity IDs, modes, and setpoints were preserved, with no new
unavailable entities. The per-thermostat toggle was validated: turning one
device off removed only its MQTT readings and used cloud fallback while the
other stayed on MQTT; turning it back on restored MQTT readings for both.
Continuous MQTT availability remains under live validation.

## 0.5.2 (2026-10-12)

- Move cloud and HomeKit protocol implementations into the independent
  `mysa-client` package and consume its versioned API. Keep HA lifecycle,
  routing, persistence, and state-confirmation policy in the integration.
- Move protocol tests, API references, and sanitized cloud examples into the
  client repository; retain integration regression coverage and enforce the
  package boundary. Existing configuration and pairing data remain compatible.
- Add the client logger to HA debug captures and support local wheel or editable
  client installs in the development setup script.

Validated with 260 integration tests against `mysa-client[homekit]==0.1.0`
installed from PyPI, plus 89 standalone client tests on Python 3.11–3.14.
Formatting, Ruff, strict mypy, documentation, and package checks pass.

Built and installed on Home Assistant 2026.10.1 on 2026-10-12. Both Baseboard
Lites reconnected locally after restart and cloud polling succeeded. Display
units were changed through HomeKit and restored; cloud sleep-brightness changes
from 50% to 55% and back received device-report confirmation on both thermostats.
Heating modes and setpoints were unchanged. Saved configuration and entity IDs
were preserved, with no new unavailable entities, Mysa startup errors, or
diagnostics capture failures. All 41 deployed files matched the release build.
Newer-model hardware and induced failure paths remain untested live.

## 0.5.1 (2026-10-12)

- Add bounded, memory-only command and event history to diagnostics, with anonymous thermostat labels, command correlation IDs, requested/baseline values, HTTP route/status/duration, lifecycle transitions and source/fallback/backoff evidence. Retain superseded commands and distinguish acceptance, stored configuration and device reports from physical application.
- Sanitize events before buffering/logging and again on export; omit unknown payload fields and raw exception/server text. Update debug-capture instructions and V3/model/bug reporting for HA-only reproduction and downloads before reload.
- Fix the implemented-coverage table rendering in the cloud implementation guide and clarify the experimental-model setup wording.

Validated with 337 automated tests, including diagnostics HTTP downloads, seeded-secret checks, retention bounds, cancellation, cloud fallback and late HomeKit write correlation. Ruff, strict mypy and offline documentation checks pass.

Installed on Home Assistant 2026.10.1 on 2026-10-12 and validated in Google Chrome. Downloaded diagnostics and the debug log through HA's integration page. The diagnostics contained 30 events with no capture failures, including two cloud brightness commands with distinct correlation IDs, HTTP 200 results and device-report confirmation. Brightness was restored from 55% to its original 50%; heating settings were unchanged and debug logging was disabled. Neither download contained any of the 24 checked stored credentials and personal identifiers. HA's debug export also contained unrelated startup messages, so the full log still needs review before sharing. Both Baseboard Lites remained locally connected; newer-model hardware and induced failure paths remain untested live.

## 0.5.0 (2026-10-12)

- Import app-enrolled Baseboard V3, Central HVAC and Climate Sensor independently of local pairing eligibility. Mark newer-model cloud support experimental.
- Add capability-gated V3 controls, sensor-only Climate Sensor entities, Central HVAC AUTO target pairs, native fan controls and fresh equipment activity. Validate model-specific commands and preserve pending versus reported state.
- Exclude legacy schedule/usage/firmware/settings routes and deferred extras for these models, including native schedule queries in mixed-model accounts. Add synthetic APK-derived fixtures, HA regression coverage and a V3 hardware test guide. No new hardware validation is claimed.

- Read structured and legacy cloud state independently, with per-field clocks, provenance, invalid-value rejection, partial-failure retention and bounded host fallback.
- Track accepted cloud commands separately from reported state, including supersession, expiry, stored configuration and device confirmation. Dependent writes use pending intent without presenting it as confirmed state.
- Add explicit settings-route alternatives, native v1/v2 schedule inspection/cleanup with persistent rollback and shared-device preservation, and strict discovery validation.
- Send the required JSON `{}` for firmware triggers. Add independent optional-request backoff, verified live-reporting freshness and source/command diagnostics.

- Retry readback twice when a cloud command is still pending after the initial two-second read. This fixes stale limit controls when Mysa reports a successful change several seconds later; commands are never resent automatically.

- Refresh HomeKit characteristic addresses, limits, supported modes and subscriptions after configuration changes/reconnects and every 15 minutes. Missing-characteristic reads receive one bounded refresh/retry; removed fields lose their cached values and subscriptions. Polling remains available when event subscription fails.
- Discover capability-dependent entities after local recovery, including the display-unit selector after a cloud-disabled offline startup, without reloading or duplicating entities.
- Retain incomplete HomeKit Device migration markers so setup can retry after unload/takeover failure while preserving entity IDs and existing device metadata.

Validated with 319 automated tests, Ruff, strict mypy and documentation checks. Installed on Home Assistant 2026.10.1 and visually checked in Chrome. Both Baseboard Lites reconnect locally; setpoints, display units, lock, brightness, limits and offset were exercised and restored. Temporary schedule creation/replacement, timed hold, resume and native v2 cleanup were verified; no test schedules or holds remain. Saved configuration and entity IDs are preserved.

Newer-model support, HomeKit metadata-change/migration failure cases, ambiguous cloud failures and firmware installation remain covered by simulations rather than new hardware tests. V3, Central HVAC and Climate Sensor support is experimental; enroll through the Mysa app first. Matter and direct enrollment for these models are not included.

## 0.4.5 (2026-10-12)

- Fixed schedule holds that replaced an explicit OFF with the current mode or heat. Omitting the mode now preserves OFF when the thermostat is already off. OFF holds send no temperature target or immediate setpoint command, including when a temperature is supplied.
- Climate setpoint controls now use Home Assistant's display units for their step size. The usual 0.5 °C step becomes 1 °F in Fahrenheit; other capability-provided steps are converted as temperature intervals. The thermostat's screen units do not affect this choice. Commands and readings retain their existing Celsius conversion and precision.
- Added regression coverage for timed and permanent holds, Fahrenheit command conversion and limits, an In-Floor capability allowing 40 °C, AC startup with reported OFF and desired COOL, and numbered AC fan speeds and their HTTP commands.

Validated with 252 automated tests (90% coverage), Ruff and strict mypy on Home Assistant 2026.9.4. AC and In-Floor regression checks use simulated responses; this release does not add hardware validation for those models.

Installed locally on Home Assistant 2026.10.1 on 2026-10-12. Both Baseboard Lite thermostats connected over HomeKit after restart; temporary setpoint writes were confirmed and original settings restored. Cloud polling succeeded, saved configuration and entity IDs were unchanged, and there were no new unavailable entities or Mysa startup errors.

## 0.4.4 (2026-10-11)

- Cloud readings now count as current for up to 90 minutes instead of ten. Outside live mode, Mysa thermostats upload their readings in batches 12–30 minutes apart, so humidity, Wi-Fi signal and the humidity on the climate entity were unavailable for most of each interval. Cloud fallback uses the same limit.
- Live mode during local outages is on by default for new setups. Existing setups keep their saved choice; change it under integration options → Cloud and polling.

Validated with 219 automated tests, Ruff and mypy. The freshness change follows live checks on two Baseboard Lite thermostats: Mysa's raw usage history had samples about 31 seconds apart, but the newest uploaded sample always matched `latestTelemetry`, which advanced in batches up to 30 minutes apart.

## 0.4.3 (2026-10-11)

- Separated initial setup, thermostat subentry management, and integration options/account management into focused flow modules. Existing setup steps, settings, and credential formats are preserved.
- Made shared thermostat state explicit and local to each setup, subentry, or repair flow, including discovery, pairing recovery, and account-linking choices.
- Split flow tests along the same boundaries, added a state-isolation regression test, and documented flow ownership.

Validated with 218 automated tests (90% coverage), Ruff, and mypy. Live sanity checks on Home Assistant 2026.10.1 covered the setup guard, integration options/account menus, thermostat add/reconfigure forms, both Baseboard Lite local connections, and cloud updates. All 42 active entities were available; stored configuration, entity IDs, and thermostat setpoints were unchanged.

## 0.4.2 (2026-10-11)

- Separated cloud HTTPS/authentication and HomeKit communications into independent protocol packages. HomeKit continues to use aiohomekit. Protocol code has no Home Assistant imports or dependency on the other protocol.
- Added semantic protocol operations and read-only HomeKit state so entities and setup flows no longer construct wire payloads or access raw HomeKit objects.
- Isolated HA polling/lifecycle adapters, command ordering/fallback, and durable pairing storage. Existing credential formats, entity IDs, delayed-write reconciliation, and schedule recovery are preserved.
- HomeKit takeover rolls back an ambiguous pairing addition after a lost response or cancellation, with bounded cleanup that preserves the original error.
- Added architecture documentation and automated dependency-boundary checks.

Validated with 217 automated tests (90% coverage), Ruff and mypy, and two connected Baseboard Lite thermostats on Home Assistant 2026.10.1. Live checks covered HomeKit setpoints and display units, concurrent commands, cloud brightness and direct setpoint writes confirmed through HomeKit, and temporary schedule creation/replacement/removal. Original settings were restored; no device was unpaired or deliberately taken offline. AC-specific features remain covered by simulated tests.

## 0.4.1 (2026-10-11)

- Commands to each thermostat are serialized. A delayed local write is reconciled with newer commands after cloud fallback, including ambiguous responses and integration unload.
- HomeKit pairing credentials are saved immediately after pairing succeeds. Interrupted setup can resume with those credentials after an HA restart.
- Schedule replacement saves a persistent backup before detaching the original. Failed requests reconcile with the server and restore the original when needed; pending recovery survives restarts and protects newer Mysa app edits and shared schedules.
- Cloud climate availability now requires an explicit online status and telemetry no more than ten minutes old. Missing timestamps and timestamps more than a minute in the future are rejected.
- Cloud settings controls remain usable when telemetry is old but the cloud responds and reports the thermostat online. Humidity and Wi-Fi signal still require fresh telemetry.
- HomeKit errors and missing values for essential thermostat characteristics mark the local connection unavailable until a successful read confirms recovery. Errors in optional display-unit reads do not disable climate control.
- Cloud duty cycle is treated as an interval fraction: estimated power uses wattage multiplied by that fraction, and fallback runtime uses measured usage history instead of counting every nonzero duty cycle as continuously heating.
- Hardware setpoint limits fall back to the cloud settings record when the reported state omits them, fixing unknown limit controls on a Baseboard Lite.
- Schedule recovery recognizes the names, weekday types, event order and client metadata normalized by Mysa's legacy schedule API.

Validated on two Baseboard Lite thermostats with firmware 3.17.5.13 and Home Assistant 2026.10.1: local setpoints and display units, concurrent commands, cloud brightness and setpoint writes, temporary schedule create/replace/clear, and relay power/runtime. Original settings were restored. Pairing interruption, induced outages and lost-response failure paths use automated simulations; no devices were unpaired, removed or network-blocked during live validation.

## 0.4.0 (2026-10-11)

- Added cloud controls for Mini-Split AC fan speed and vertical and horizontal swing to the climate entity. Fan options follow the remote: named speeds (low/medium/high) or numbered speeds (1–7), with auto or sleep where available. Swing options include off, on, auto or fixed positions where supported.
- Each control appears only when the AC remote supports it in the current mode. Controls are hidden while the AC is off to avoid turning it on. Remotes with only a swing toggle are not supported.
- The AC implementation follows Mysa Android app 4.20.1 and has been tested with simulated responses. It has not been tested on AC hardware.

## 0.3.1 (2026-10-11)

- Live mode during local outages is now an option under integration options → Cloud and polling. It is off by default. When enabled, Mysa is asked to report about every five seconds and the cloud is polled every 10 seconds while a locally paired thermostat is unreachable on the LAN. Earlier versions enabled this automatically. When disabled, cloud polling uses the normal interval, so readings may be several minutes old.

## 0.3.0 (2026-10-11)

- Baseboard V3, Central HVAC and Climate Sensor are now excluded from pairing and account imports because setup requires credential files from Mysa's servers. HomeKit discovery no longer claims these models. The account device table marks them unsupported.
- Signing in can optionally save the Mysa password. This is off by default. If token refresh is refused, the integration signs in again with the saved password or asks the user to sign in again. The cloud client supports this through `MysaAuth(email=..., password=...)`.
- A saved password is stored unencrypted in Home Assistant's configuration, redacted from diagnostics and deleted on sign-out. The options page shows whether it is saved; sign out and back in to change the choice. Reauthentication preserves that choice and replaces the saved password with the new one.
- Added a humidity sensor and current humidity on the climate entity when cloud state includes humidity. Baseboard Lite reports humidity even though its capability data says it has no humidity sensor. Its calculated dew point was within 1 °C of a separate room sensor.
- Added a heater power sensor in watts when heater wattage is configured. It reports the configured wattage while the relay is on and 0 W while it is off. It is created with the heater energy sensor.
- Added a Mysa Local section under Settings → System → Repairs → menu → System information. It shows thermostat counts by connection path, sign-in and saved-password status, cloud status, the poll interval and whether Mysa's server can be reached. Server reachability is checked only when cloud access is enabled.
- A 403 or a 401 after token refresh is now treated as an API error instead of a request to sign in again. A refused refresh can trigger a new sign-in. A refused claim for a thermostat on another account still displays the existing add-error message.
- Heating runtime today now uses the larger of the local count and the thermostat's figure. This fixes decreases that Home Assistant treated as resets, causing double-counting in statistics. Runtime accounting also handles heating across midnight. Runtime and heater energy update once a minute.
- Diagnostics now redact the HomeKit name (which includes part of the Wi-Fi MAC) and other text containing the device ID.
- The device page retains its serial number and firmware version if HomeKit and cloud are unavailable at startup. Firmware updates now appear without restarting Home Assistant.
- Turning off cloud access disables cloud-only entities while preserving their names, entity IDs, areas and history. Turning it back on re-enables them and reloads the integration about 30 seconds later. Entities disabled by the user remain disabled.
- Cloud response parsing now uses the thermostat's reported values instead of pending requests.
- Wake and sleep brightness now follow Mysa app rules: steps of 5, wake brightness of at least 10% and sleep brightness no greater than wake brightness. Changing one value adjusts the other when needed.

## 0.2.1 (2026-10-11)

- Updated repair warning translations to pass Home Assistant's `hassfest` check. Warnings with a fix show their text in the fix flow; unsupported-model warnings use a separate text-only message because Home Assistant cannot fix them.

## 0.2.0 (2026-10-11)

- While signed in to Mysa, setup now offers to add a locally paired thermostat to the account or pair an account thermostat locally. Local pairing discovers the device by its `Mysa-xxxxxx` network name.
- Each thermostat's Settings and status menu shows whether it is on the Mysa account and paired locally. It can add or remove the thermostat from the account or start local pairing. The options page lists account thermostats with their Home Assistant and Mysa app names; the table is view-only.
- The device's model line shows its Mysa ID and, when different, its Mysa app name. Device settings can sync the name in either direction.
- Added repair issues for thermostats paired locally but missing from the account, available only through the cloud or removed from the account after being imported. Each issue can be ignored.
- Added diagnostic sensors for Mysa account membership and HomeKit pairing. The options menu also identifies the signed-in Mysa account.
- Added cloud client methods to claim, delete and replace a device, get shortcuts with `get_shortcuts()` and update them with `update_shortcut()`. Home names are available through `Bootstrap.homes`. `replace_device()` calls the legacy `purgeadd` route and is not used by the integration. `add_device()` returns `False` when the API responds with 409 because the device is already on the account.
- The integration uses `add_device(app_defaults=False)` to omit the newer app's settings defaults when claiming a returning thermostat. Including those defaults had reset its heater type to Baseboard during testing. Claim defaults cover Baseboard V1, Baseboard V2 and Lite, In-Floor and AC. Unknown models and models requiring Bluetooth credential files raise `ValueError`. See the [cloud API documentation](docs/cloud-workflows.md#adding-and-removing-devices) for the limits of settings retention.
- Added the `ModelFamily`, `MODEL_FAMILIES`, `model_family()`, `WIFI_ONLY_FAMILIES` and `DutyCycleOpt` constants and helpers.

## 0.1.4 (2026-10-11)

- The pairing flow now explains that an unpaired thermostat's HomeKit service may sleep and can be woken by pressing a thermostat button. A connection failure keeps setup open and tells the user to wake the thermostat and retry.

## 0.1.3 (2026-10-11)

- The HomeKit setup code accepts any spacing or punctuation and uses its eight digits. Codes with the wrong number of digits now explain that the code has eight digits and is printed on the back of the faceplate. Trivial codes such as `11111111` get a separate error.

## 0.1.2 (2026-10-11)

- The setup and options flows now mark Mysa sign-in as optional. The sign-in form lists the features it enables.

## 0.1.1 (2026-10-11)

- Diagnostics now redact the thermostat's HomeKit ID and Mysa device ID (its Wi-Fi MAC).

## 0.1.0 (2026-10-11)

First release. Tested on a Mysa Baseboard Lite (`BB-V2-0-L`) with firmware `3.17.5.13`.

- Added local HomeKit control for setpoint, mode, room temperature, heating state, display units and Identify. The integration claims Mysa models during HomeKit discovery so users can pair from the discovery card.
- Added migration from HomeKit Device without the setup code. Migration transfers matching climate, temperature, display-units and Identify entities with their IDs and registry settings, unless an equivalent Mysa Local entity already exists. It copies device names, areas and labels, then removes the old entry.
- Optional Mysa sign-in uses Cognito SRP and saves a refresh token without storing the password. It adds button lock, wake and sleep brightness, hardware setpoint limits, temperature offset, heater type, firmware checks, cloud connectivity, Wi-Fi signal and the thermostat's heating runtime.
- Controls supported by HomeKit use it first and fall back to the cloud. Other controls use the cloud directly. The integration checks whether a timed-out write took effect before retrying through the cloud. A connection-path sensor shows the active route. A repair issue appears after 15 minutes without the local connection.
- While a locally paired thermostat is unreachable on the LAN, the integration requests Mysa live mode and polls every 10 seconds. It stops renewing live mode and returns to normal polling when the local connection is restored. Live mode was automatic in this release; it became an opt-in setting, off by default, in 0.3.1.
- One batch request polls cloud state for all thermostats. Other cloud data uses separate requests. Polling backs off after errors and rate limits. Disabling cloud access stops the integration's requests to Mysa's servers.
- Added heating runtime for the current day, read from the thermostat or counted locally, and daily heater energy based on a configured wattage for the Home Assistant Energy dashboard.
- Added the `hold`, `resume_schedule`, `set_schedule` and `clear_schedule` actions and a sensor that reports schedule holds. The `hold` action requires a Mysa schedule on the thermostat.
- Added a firmware update entity. Firmware checks run when cloud access is enabled; installation is opt-in under Advanced options.
- Added diagnostics with redaction, translations and brand images.
