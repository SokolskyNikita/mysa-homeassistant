# Protocol example conventions

These files are inert documentation data. Offline checks parse them and follow
local links; they never log in, discover a device, or replay a request. Start
with the [cloud reference](../../custom_components/mysa_local/protocols/cloud/README.md)
and [MQTT reference](../mqtt.md) for conclusions and scope.

## Format and provenance

HTTP examples contain `id`, `kind`, `evidence`, `request`, and `response`.
Method, base URL, path, query, headers, authentication mechanism, and body are
separate. A missing `body` means no HTTP body; `body: null` means JSON null;
`body: {}` means an empty JSON object. `body` and `bodyFile` are mutually
exclusive. A `bodyFile` path resolves relative to the example file.

Response `representation` is `complete`, `projection`, `fixture`, or `unknown`.
`status: null` and a missing body mean the specific status/payload was not retained;
they are not a successful response or JSON-null reply. Do not reconstruct unknown
responses. A projection includes selected fields only. Request query maps describe
values separately; preserve exact path/trailing slash and explicit encoded queries
when the evidence depends on their wire encoding.

Evidence kinds are `live-sanitized`, `app-derived`, and `constructed`. The current
examples are constructed or app-derived, not live captures. Constructed exchanges
illustrate documented shapes; they are never additional validation. App-derived
examples name the app release and describe protocols reverse-engineered from the
original Mysa apps. Unknown dates/model/firmware stay null. Existing sanitized
regression fixtures are referenced rather than copied and do not establish live
behavior.

Identifiers are synthetic: device `a1b2c3d4e5f6`, UUIDs ending `a1`/`b1`, and
resource names ending `example`. Fixture room labels are illustrative. Times
are illustrative unless an example explicitly says otherwise; preserve interval
relationships, seconds versus milliseconds, and the future-time anomaly. No
credentials, signed URLs, pairing keys, real household IDs, or Wi-Fi passwords
belong in examples. Public Cognito pool/client identifiers are configuration,
not secrets. The refresh-token placeholder cannot authenticate.

MQTT examples use direction, topic, QoS, retain, payload, and correlation metadata.
The two command encodings are app-derived; no live acknowledgement is invented
for a rejected publish. BLE remains documented as binary bytes, not JSON.

## HTTP exchanges

| Example | Purpose |
| --- | --- |
| [bootstrap.success](cloud/bootstrap.success.json) | bootstrap.success |
| [devices.success](cloud/devices.success.json) | devices.success |
| [capabilities.lite](cloud/capabilities.lite.json) | capabilities.lite |
| [state-batch.success](cloud/state-batch.success.json) | state-batch.success |
| [usage.summary](cloud/usage.summary.json) | usage.summary |
| [state-history.success](cloud/state-history.success.json) | state-history.success |
| [state-update.setpoint](cloud/state-update.setpoint.json) | Request a heating setpoint |
| [schedule-create.full-body](cloud/schedule-create.full-body.json) | Create and attach a modern schedule |
| [schedule-update.full-body](cloud/schedule-update.full-body.json) | In-place modern schedule update |
| [hold.permanent](cloud/hold.permanent.json) | Permanent hold with future start |
| [shortcut.replace-actions](cloud/shortcut.replace-actions.json) | Replace shortcut action map |
| [schedule-v1-clear.modern-host](cloud/schedule-v1-clear.modern-host.json) | Clear v1 events via modern-host |
| [schedule-v1-clear.legacy-host](cloud/schedule-v1-clear.legacy-host.json) | Clear v1 events via legacy-host |
| [schedule-v2-update.full-body](cloud/schedule-v2-update.full-body.json) | Legacy v2 schedule body |
| [schedule-v2-delete.with-body](cloud/schedule-v2-delete.with-body.json) | Delete v2 using schedule ID and JSON body |
| [device-claim.minimal](cloud/device-claim.minimal.json) | Minimal returning-Lite claim |
| [cognito-refresh.request](cloud/cognito-refresh.request.json) | Cognito token-refresh request |
| [live-mode.request](cloud/live-mode.request.json) | live-mode.request |
| [usage.expanded](cloud/usage.expanded.json) | usage.expanded |
| [state-update.ac-fan](cloud/state-update.ac-fan.json) | state-update.ac-fan |
| [state-update.ac-swing](cloud/state-update.ac-swing.json) | state-update.ac-swing |
| [state-update.remote-tracking](cloud/state-update.remote-tracking.json) | state-update.remote-tracking |
| [firmware-availability.no-update](cloud/firmware-availability.no-update.json) | Firmware availability, no release offered |
| [firmware-trigger.empty-object](cloud/firmware-trigger.empty-object.json) | Accepted firmware check with empty JSON object |
| [firmware-trigger.no-body-error](cloud/firmware-trigger.no-body-error.json) | Rejected firmware trigger without body |
| [iot-policy.limited](cloud/iot-policy.limited.json) | Attached policy document projection |
| [legacy-state.mixed-time](cloud/legacy-state.mixed-time.json) | Constructed legacy per-field timestamp illustration |
| [state.future-telemetry](cloud/state.future-telemetry.json) | Constructed future telemetry versus older configuration |

## MQTT messages

- [Lite command with array encoding](mqtt/thermostat-command.array.json)
- [Lite command with string encoding](mqtt/thermostat-command.string.json)

Run `python3 tools/check_docs.py` from the repository root. Fixture changes must also consider the API/parser tests consuming those payloads.
