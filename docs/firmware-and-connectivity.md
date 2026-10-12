# Firmware and device connectivity

The [cloud protocol reference](../custom_components/mysa_local/protocols/cloud/README.md) owns endpoint availability, S/R/L terminology, observed limitations, and recommended client policy. This page supplies detailed wire mechanics; implementation status belongs to the [cloud tracker](implementation/cloud.md).

The protocols were reverse-engineered from the original Mysa apps. Hardware observations cover Baseboard Lite `BB-V2-0-L`, firmware `3.17.5.13`. Other-model behavior remains app/code-derived unless explicitly stated.

## Thermostat startup and connectivity

Observed on Baseboard Lite (`BB-V2-0-L`) firmware `3.17.5.13`:

| Connection | Destination | Behavior |
| --- | --- | --- |
| Network setup | Local Wi-Fi and DHCP | Joins the network and obtains an address independently of cloud availability |
| Time synchronization | `time.nist.gov` → `ntp1.glb.nist.gov`, UDP 123 | Requests time about every 15 seconds |
| Cloud messaging | `a3q27gia9qg3zy-ats.iot.us-east-1.amazonaws.com`, TCP 8883 | Opens an MQTT/TLS connection; the normal connection exchanges keepalive traffic about every minute |
| Firmware check | `devices10010.getmysa.com`, TCP 443 | Contacts this host during startup and after a cloud update trigger; the HTTP path remains unknown |

When cloud connections fail, the thermostat can remain joined to Wi-Fi. MQTT
retries include fresh DNS lookups and different broker addresses, settling to
roughly one retry cycle per minute in the observed outage. A successful MQTT
connection was not required for the startup firmware check. Restoring normal
network access allowed cloud messaging and real-time NTP replies to resume
without another reset.

The firmware HTTPS client rejected an untrusted server certificate before
sending an HTTP request. Neither blocked cloud ports, failed firmware/MQTT DNS
lookups, nor that TLS failure produced an observed HTTP/80 fallback during the
bounded observations. This does not rule
out behavior on other firmware or after a longer outage.

## Firmware

### Legacy devices

For V1/V2/Lite, In-floor and AC, `GET /devices/update_available/{dev}` returns:

[Firmware availability exchange](examples/cloud/firmware-availability.no-update.json)

Both control generations use the same availability endpoint and
`GET /devices/firmware` for installed versions. Their update triggers differ:

| Path | Trigger and completion |
| --- | --- |
| Current HTTPS | `POST /devices/{dev}/firmware-update` with JSON `{}` (live-verified); poll availability until `update` is false |
| Legacy MQTT | `MsgType: 6, EventType: 2`; poll the same availability endpoint, with boot messages `MsgType: 10` or MCM `msg: 61` as additional completion signals |

The phone requests an update; it does not transfer the firmware image. Reference
clients poll every 10–20 seconds and may repeat the trigger while an update is
available, with a six-minute limit in the current pairing flow. BLE credential
transfer is documented [separately](bluetooth.md#credential-transfer-on-newer-models).

Compare [JSON `{}`](examples/cloud/firmware-trigger.empty-object.json) with
[no request body](examples/cloud/firmware-trigger.no-body-error.json).
With no update available, the trigger returned HTTP 200 with
`{"triggered":true,...}`, followed by incoming traffic on the thermostat's
existing MQTT/TLS connection and an HTTPS connection to
`devices10010.getmysa.com:443`. This identifies the host contacted for the check,
not a firmware-image URL or the app-facing `/ota/*` paths. No firmware download,
reboot or version change occurred; installation of an available release remains
unverified. See [startup and connectivity](#thermostat-startup-and-connectivity)
for the device's network behavior.

HA intends to send one trigger and poll every 20 seconds for up to six minutes,
when firmware installation is enabled. The current request omits the required
JSON `{}`; this known client gap is tracked as [C-A01](implementation/cloud.md#c-a01).
The accepted protocol request does not establish a working integration update.

### Newer OTA service

The newer fleet-based OTA protocol for V3, Central HVAC and Climate Sensor uses `GET /ota/update/available/{dev}`, `POST /ota/update` with `{deviceIds,versionId,requestSource:"USER",...}`, `GET /ota/update/{requestId}` and `PUT /ota/update/{requestId}/cancel`. Fleet registration happens during pairing.

The Lite returned 404, "No fleet found containing device", from this availability endpoint. Python does not implement the new OTA service.


## Time, transport, and validation limits

Cloud telemetry has included implausible future timestamps even when other
readings were current. The cause and recovery time remain unresolved. Treat
future timestamps as invalid for current telemetry selection; HTTP success does
not establish freshness or device clock accuracy.

A Cognito credential attempt on MQTT port 8883 closed during TLS, before MQTT
or subscription. The same app identity connected/subscribed over WebSocket on
443. The thermostat's own credential material was not recovered or reproduced;
app credentials and device credentials remain distinct.

No firmware binary, reusable thermostat private key, or app-side image transfer
was identified by reverse-engineering the original apps. The setup RSA public key encrypts Wi-Fi
credentials, and AC `mqttFileDelivery`/IR signatures concern remote codes, not
thermostat firmware. `CUSTOM_REBOOT_REASONS.REBOOT_URL_MANAGER = 10` is a
reason label, not a command. MCM `REQUEST_REBOOT = 8` has no recovered app sender;
legacy `MsgType: 10` is a boot notification, and MCM `msg: 10` is a different enum.

The device's firmware-check HTTP path and installation of an offered release
remain unverified. Newer-model OTA, credential transfer, and device-level MQTT
authentication also remain unvalidated.

## Sources

Existing network observations are from October 11–12, 2026. App evidence uses
Android 3.26.0/3.54.1 for legacy notifications and 4.20.1 for HTTPS and fleet OTA.
App behavior does not establish the behavior of the device's downloader. See the
[documentation maintenance guide](maintenance.md).
