# MQTT telemetry in Mysa Local

When Mysa sign-in and **Use Mysa cloud** are enabled, the default-on integration
option **Live MQTT readings** uses the optional client from
[`mysa-client`](https://github.com/SokolskyNikita/mysa-client) to receive live
telemetry. Each thermostat also has a default-on **Use live MQTT readings**
setting under its **Settings and status** menu. One connection is shared across
account thermostats with that device setting enabled. The integration option is
the master switch; turn it off to stop MQTT for all devices, or turn off a
device setting to exclude only that thermostat. The client itself has no
per-device enable flag: the integration supplies the allowlist of enabled
account devices. Live reporting is renewed every 150 seconds. A device that
has reported telemetry but then goes quiet gets an earlier refresh after
20 seconds, before its readings expire. Devices that have not produced a
reading keep the slower retry cadence. This path is read-only in Home Assistant: commands still use
HomeKit or the existing HTTPS cloud path, and MQTT failure does not block setup
or local use.

The integration prefers MQTT humidity and numeric duty-cycle values received
within the last 30 seconds. Freshness uses local receipt time. Each field falls
back independently to a valid HTTPS value after MQTT data expires or becomes
unavailable; duty cycle is unavailable unless one source supplies a numeric
value. Estimated heater power uses a fresh MQTT duty-cycle fraction before the
existing local/cloud behavior. Home Assistant exposes the numeric duty cycle
as a percentage when a value is available. Humidity and duty-cycle sensor
attributes identify whether the current value came from MQTT or cloud.

No separate broker, Home Assistant MQTT integration, or HomeKit pairing code
is required for this feature. A Mysa account and enabled cloud access are
required. Current live MQTT hardware evidence covers two Baseboard Lite devices
on one account; other models and accounts remain unverified.

The October 12 coordinator follow-up received both humidity and duty cycle
from both test devices, but telemetry was irregular: some gaps exceeded
30 seconds, and a later connection stopped acknowledging requests while its
broker-connected flag was still true. Early refresh improved quiet-device
updates in that test, but continuous availability is not established. Expiry
and fallback remain necessary. Those coordinator tests ran from the development
machine before deployment.

The initial deployment of Mysa Local 0.6.0 and `mysa-client` 0.1.2 on Home
Assistant 2026.10.1 received MQTT humidity and numeric duty cycle from both
installed Baseboard Lite thermostats; both reported `source: mqtt`. The release
added two duty-cycle sensors, taking the active entity count from 42 to 44.
Saved configuration, sign-in, pairings, existing entity IDs, modes, and
setpoints were preserved, with no new unavailable entities. This validates the
default-enabled telemetry path on this setup. The per-thermostat setting was
then tested through Home Assistant: disabling one thermostat removed its
subscription and MQTT readings, and humidity/duty cycle fell back to cloud;
the other thermostat remained subscribed with both values sourced from MQTT.
Re-enabling the first restored MQTT-sourced humidity and duty cycle on both.
After restoring the HA MQTT session, a 200-second observation received 13
successful samples at 15-second intervals. Both thermostats had fresh MQTT
humidity and duty-cycle readings in every sample, with zero dropped events.
This confirms fresh readings beyond the 180-second requested stream duration;
long-term availability is not established.

The integration requires `mysa-client[homekit,mqtt]==0.1.2`. MQTT is
supplementary, and its live behavior remains intermittent; keep the 30-second
expiry and per-field fallback enabled.

The maintained MQTT authorization findings, app-derived wire format, examples,
and passive diagnostic instructions live in the standalone client repository:
[Legacy Mysa MQTT protocol](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/mqtt.md).

## Moved sections

These links preserve the original section bookmarks.

| Maintained client reference |
| --- |
| <a id="connection-and-authorization-model"></a>[Connection and authorization model](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/mqtt.md#connection-and-authorization-model) |
| <a id="aws-permission-controls"></a>[AWS permission controls](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/mqtt.md#aws-permission-controls) |
| <a id="live-evidence"></a>[Live evidence](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/mqtt.md#live-evidence) |
| <a id="policy-inspection"></a>[Policy inspection](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/mqtt.md#policy-inspection) |
| <a id="test-results"></a>[Test results](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/mqtt.md#test-results) |
| <a id="legacy-wire-protocol"></a>[Legacy wire protocol](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/mqtt.md#legacy-wire-protocol) |
| <a id="session-and-topics"></a>[Session and topics](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/mqtt.md#session-and-topics) |
| <a id="lite-commands-and-responses"></a>[Lite commands and responses](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/mqtt.md#lite-commands-and-responses) |
| <a id="liveness-and-notifications"></a>[Liveness and notifications](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/mqtt.md#liveness-and-notifications) |
| <a id="reproducing-the-diagnostic"></a>[Reproducing the diagnostic](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/mqtt.md#reproduction) |
| <a id="validation-criteria-for-a-future-fix"></a>[Validation criteria for a future fix](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/mqtt.md#validation-criteria-for-a-future-fix) |
| <a id="sources-and-unresolved-questions"></a>[Sources and unresolved questions](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/mqtt.md#sources-and-unresolved-questions) |
