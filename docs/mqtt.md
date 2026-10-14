# MQTT telemetry in Mysa Local

[Documentation index](README.md)

Mysa Local can use Mysa's MQTT service for live humidity and numeric heating
duty-cycle telemetry. This is an optional, read-only data path: HomeKit and
HTTPS continue to handle controls, and MQTT outages do not block setup or local
control. This page documents Home Assistant behavior and the hardware evidence;
the standalone [`mysa-client` MQTT guide](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/reference/protocol/mqtt.md)
owns protocol, authorization, and wire-format details.

## Enable MQTT

The integration uses one MQTT connection for its selected Mysa account devices.
MQTT is active for a thermostat only when all three conditions are true:

- You are signed in to Mysa and **Use Mysa cloud** is enabled.
- The integration option **Live MQTT readings** is on. It is on by default;
  turn it off to stop MQTT for every thermostat.
- The thermostat's **Use live MQTT readings** option is on. It is on by
  default and controls only that device.

On the Mysa Local integration page, open a thermostat's menu → **Settings and
status** → **Settings** to change its option. For experimental cloud-only
models, choose **Settings and status** → **Live MQTT readings**. Disabling one
thermostat removes it from MQTT requests and subscriptions while other selected
devices continue. Re-enabling it adds it back. If every device is excluded,
the integration has no MQTT devices to connect for.

The Home Assistant integration opts in to the library's optional MQTT client by
default behind these switches. Other `mysa-client` consumers must opt in
themselves and choose which devices to provide. You do not need a separate
broker, the Home Assistant MQTT integration, or another HomeKit pairing code.

## Values and fallback

For humidity and numeric heating duty cycle, a valid MQTT reading takes
priority while the MQTT connection is up and its age by Home Assistant's local
receipt clock is **less than 30 seconds**. At 30 seconds it expires. Device-reported timestamps do not set
this MQTT freshness window. Each measurement falls back independently to a
valid HTTPS cloud reading. If neither source has a usable value, that value is
unavailable.

The Humidity sensor and climate entity can use MQTT humidity. The Heating duty
cycle sensor reports a numeric percentage when MQTT or fresh numeric cloud
telemetry supplies it; its `source` attribute is `mqtt`, `cloud`, or `None` when
no numeric value is current. The numeric duty-cycle sensor is never calculated
from HomeKit's on/off heating state. Estimated heater power can use a fresh
MQTT duty-cycle fraction; when that is unavailable, its existing cloud or local
heating-state fallback applies.

The integration requests a 180-second reporting stream and normally renews an
accepted request every 150 seconds. If a device has produced telemetry and
then goes quiet for 20 seconds, HA requests a refresh before the readings
expire. A stream that has not produced telemetry stays on the normal renewal
cadence and does not trigger this early refresh. An unaccepted request is
retried after 60 seconds; connection failures retry from 60 seconds with
backoff up to 300 seconds. Requests do not extend reading freshness: values
still expire 30 seconds after receipt.

MQTT commands are not used by this integration. Setpoints, modes, and device
settings continue through HomeKit or HTTPS according to the existing routing
rules.

## Hardware validation and limits

On 2026-10-12, Mysa Local 0.6.0 with `mysa-client` 0.1.2 was installed on Home
Assistant 2026.10.1 and validated with two Baseboard Lite thermostats
(`BB-V2-0-L`, firmware `3.17.5.13`). Both reported MQTT humidity and numeric
duty cycle, and both values showed `source: mqtt`. The release added two duty
cycle sensors, increasing the active entity count from 42 to 44. Existing
configuration, sign-in, pairings, entity IDs, modes, and setpoints were
preserved.

The per-thermostat switch was tested in Home Assistant. Turning one device off
removed only its MQTT requests and subscription; that device used cloud
fallback while the other kept MQTT readings. Turning it back on restored MQTT
readings for both. In the observation after that toggle, all 14 Home Assistant
snapshots over 216 seconds showed fresh MQTT humidity and duty cycle for both,
with zero dropped events. Snapshots were taken 15 seconds apart; that is the
validator's sampling interval, not a guarantee of message cadence. A later
final HACS-release install and Core restart reconnected both devices
automatically. Their final numeric duty-cycle readings were 100% on one device
and 0% on the other.

An earlier development-machine probe had gaps longer than the 30-second
freshness window and a connection that stopped acknowledging requests while
still reporting connected. The deployed observation confirms sustained fresh
readings for this setup over the measured window, but does not establish
long-term availability. The integration expires MQTT readings after 30 seconds
and falls back per field to fresh cloud data when available; this protects
against stale values during a quiet or disconnected session. Hardware
validation currently covers only these two Baseboard Lite devices; behavior on
other models remains unverified.

## Protocol reference and older links

For authentication, permissions, message formats, standalone examples, and
client-level tests, see the [`mysa-client` MQTT guide](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/reference/protocol/mqtt.md).
Previously published section bookmarks are preserved as links to that guide:
<a id="connection-and-authorization-model"></a>[connection and authorization](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/reference/protocol/mqtt.md#connection-and-authorization-model),
<a id="aws-permission-controls"></a>[AWS permissions](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/reference/protocol/mqtt.md#aws-permission-controls),
<a id="live-evidence"></a>[protocol live evidence](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/evidence/mqtt.md#live-evidence),
<a id="policy-inspection"></a>[policy inspection](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/evidence/mqtt.md#policy-inspection),
<a id="test-results"></a>[protocol test results](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/evidence/mqtt.md#test-results),
<a id="legacy-wire-protocol"></a>[wire protocol](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/reference/protocol/mqtt.md#legacy-wire-protocol),
<a id="session-and-topics"></a>[sessions and topics](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/reference/protocol/mqtt.md#session-and-topics),
<a id="lite-commands-and-responses"></a>[Lite commands and responses](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/reference/protocol/mqtt.md#lite-commands-and-responses),
<a id="liveness-and-notifications"></a>[liveness and notifications](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/reference/protocol/mqtt.md#liveness-and-notifications),
<a id="reproducing-the-diagnostic"></a>[diagnostic reproduction](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/guides/mqtt-diagnostics.md#reproduction),
<a id="validation-criteria-for-a-future-fix"></a>[validation criteria](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/reference/protocol/mqtt.md#validation-criteria-for-a-future-fix),
and <a id="sources-and-unresolved-questions"></a>[sources and open questions](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/reference/protocol/mqtt.md#sources-and-unresolved-questions).
