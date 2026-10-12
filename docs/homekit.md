# Mysa HomeKit accessory reference

HomeKit provides the integration's local readings, temperature/mode control,
display units, and Identify. These findings apply to Baseboard Lite
`BB-V2-0-L`, firmware `3.17.5.13`, observed October 10–11, 2026. HomeKit's firmware
string can be shortened to `3.17.5`; BLE/cloud expose the full build. Other models
need their own accessory profile. The [package guide](../custom_components/mysa_local/protocols/homekit/README.md)
owns the Python API, persistence guarantees, errors, and lifecycle.

## Discovery and pairing

Discover `_hap._tcp.local.` through mDNS. The tested Lite used TCP 57959;
read the advertised endpoint rather than hard-coding it. TXT `id` is the HomeKit
accessory identity, `md` the model, `pv` the HAP version, and bit 0 of `sf` reports
unpaired availability when set. A paired advertisement does not prove the caller
has usable credentials. Wi-Fi device identity is separate from the HomeKit ID.

Pairing uses the printed HomeKit setup code and persists controller keys for
later authenticated encrypted sessions. BLE bonding and cloud enrollment are
separate. An unpaired Lite was observed to stop advertising and close its HAP
port until a wall button woke it; absent mDNS alone is not proof of a network
failure. An unauthenticated HTTP request returned 470, not a local REST API.

## Characteristic coverage

| Characteristic | Wire meaning | Lite observation |
| --- | --- | --- |
| Current Temperature (`0x11`) | Celsius | Readable; no humidity characteristic |
| Target Temperature (`0x35`) | Celsius | Writable, 5–30 °C, advertised step 0.5 °C |
| Current Heating/Cooling State (`0x0F`) | 0 idle, 1 heating, 2 cooling | Local action state; distinct from interval cloud duty |
| Target Heating/Cooling State (`0x33`) | 0 off, 1 heat, 2 cool, 3 auto | Lite advertises off/heat only |
| Temperature Display Units (`0x36`) | 0 C, 1 F | Screen units; temperatures remain Celsius |
| Identify / Accessory Information | Identify plus model/manufacturer/serial/firmware | Exposed |
| Wi-Fi Transport (`0x22A`) / Configuration Control (`0x22D`) | Configuration TLV8 | Readable; successful migration unverified |
| Accessory Runtime Information (`0x239`) | Runtime/ping/heartbeat surface | Exposed; not an additional thermostat control API |

Use advertised characteristics and ranges; instance IDs are accessory-specific.
The [sanitized accessory fixture](../tests/fixtures/homekit_bb_v2_0_l.json)
records the profile used by tests. Its payload is a regression fixture, not proof
of validation on another model. Button lock, brightness, schedules, and humidity
were absent from the tested Lite's HomeKit surface; the cloud supplies those
supported functions. HomeKit target modes are not the cloud mode enum.

The vendor service `3354EC82-AF38-4755-B4A4-4DB8E418F555` contains hidden writable
boolean `E71D8348-BB33-4C34-8C50-A64B1136EDD2`. A historical true/false trial
retained the value without an observed display, button-lock, reboot, or MQTT
change. Its purpose is unknown. Do not expose it as a named feature.

## HomeKit Wi-Fi switching

Wi-Fi Configuration Control operation 1 reads worked. Timed TLV8 operation 3
(fail-safe update) writes with a fresh cookie and 120-second timeout returned
HAP status 0, matching cookie, and pending/restart flags. The corrected test AP
was independently visible and active; the Lite rejoined its original AP after
about 5–6 seconds, with no target authentication observed. Keeping the controller
object open did not change the result. No operation 4 commit was sent.

| TLV8 field | Encoding |
| --- | --- |
| Operation | Tag 1, unsigned byte: 1 read, 2 simple update, 3 fail-safe update, 4 commit |
| Cookie | Tag 2, fresh nonzero little-endian uint16 |
| Timeout | Outer tag 4, 120 seconds in the recorded trial |
| Station configuration | Outer tag 11; nested tags 1/2/3 are UTF-8 SSID, WPA2 security mode 1, and UTF-8 passphrase |
| Update status | Tag 3: low 16 bits cookie; `0x10000` pending; `0x20000` session restart required |

The recorded write used `PUT /prepare` with a 5000 ms TTL and fresh PID, then
`PUT /characteristics` with that PID, Base64 TLV8 value, and `r: true`.

The recorded sequence followed [Apple controller code](https://github.com/EthanArbuckle/iPhone17-1_18.2_22C152_Restore/blob/e26ed4563f78871c59d2d96856756a65d62517e5/System/Library/PrivateFrameworks/HomeKitDaemon.framework/HMDWiFiManagementController.m#L708).
Readback can include a Wi-Fi password; do not publish raw responses. This is an
accepted staged request, not a demonstrated migration or a proven firmware bug.
Pairing and normal control remained usable. Operation 2, operation 4 completion,
and another AP/firmware remain unverified.

## Implementation and validation limits

Mysa Local implements discovery, pairing, saved-pairing loading/copy/removal,
semantic thermostat reads/writes, events, and Identify. The adapter does not
perform Wi-Fi migration or implement the hidden boolean. Package tests use fakes;
real Lite observations establish the narrower hardware scope above. For command
confirmation and fallback ownership, see [architecture](architecture.md).
