# Mysa Bluetooth protocol

Mysa uses Bluetooth Low Energy (BLE) and GATT for Wi-Fi provisioning. The known
interface exposes device information, nearby Wi-Fi networks, and a transaction
for sending network credentials. Newer models also receive a cloud-issued
credential file over BLE. No Bluetooth interface for normal thermostat control
has been established.

**To connect a thermostat to Wi-Fi, use the
[Mysa Wi-Fi setup page](https://sokolskynikita.github.io/mysa-wifi-setup/).**
Its [source and usage guide](https://github.com/SokolskyNikita/mysa-wifi-setup)
provide the working browser implementation. The legacy setup flow works without
the Mysa app or a Mysa account. This page is a protocol reference, not a
Bluetooth implementation inside Mysa Local.

## Scope and compatibility

The protocols were reverse-engineered from the original Mysa apps, with hardware validation
on a **Baseboard Lite (`BB-V2-0-L`), firmware `3.17.5.13`**, as of October 11,
2026. Wi-Fi provisioning and network listing work on that Lite. The common
Wi-Fi packet format is unchanged between the examined 3.54.1 and 4.20.1 apps;
the older 3.26.0 app also contains the provisioning services.

| Family | Known setup path | Validation |
| --- | --- | --- |
| Baseboard Lite | BLE Wi-Fi provisioning, then optional HomeKit pairing and cloud account enrollment | Tested on hardware |
| Other legacy models: Baseboard V1/V2, In-floor, AC | App contains legacy provisioning paths, including BLE and temporary Wi-Fi access point setup | BLE availability must be checked on the particular model; not all models are established to expose these services |
| Baseboard V3, Central HVAC, Climate Sensor | BLE Wi-Fi provisioning plus cloud-issued credential transfer and registration | App-derived; not tested on hardware here or implemented by the setup page |

Bluetooth bonding, joining Wi-Fi, enrolling in a Mysa account, and pairing with
HomeKit or Matter are separate operations. Success at one step does not establish
success at the others.

## Connection and security

The thermostat must be in Bluetooth setup mode, indicated by its Bluetooth icon.
Use the [setup guide](https://github.com/SokolskyNikita/mysa-wifi-setup#steps)
for the user-facing sequence. The tested Lite advertised as `Mysa` and exposed
a name of the form `Mysa-xxxxxx` after connection. Discover by the Wi-Fi service
UUID where possible; the advertised name is not a unique device identity.

Connect, discover the Wi-Fi configuration service, and subscribe to its status
notifications before sending commands. Reading the protected status
characteristic triggers the operating system's Bluetooth pairing flow when
needed. The thermostat displays a code for confirmation with the phone or
computer. Existing bonds can be reused; a successful read on a previously paired
computer does not demonstrate unauthenticated access. The binary Identify value
described below is not an established pairing code.

Wi-Fi strings are Base64-encoded, **not encrypted by the packet format**. Link
security comes from Bluetooth pairing and encryption. Clients should avoid
logging passwords or credential-file contents. Bluetooth bonding is independent
of the printed HomeKit setup code and of Mysa cloud authentication.

## GATT services

### Device Information

The standard service `180a` exposes these read-only UTF-8 characteristics on the
tested Lite. Short UUIDs use the Bluetooth base UUID
`0000xxxx-0000-1000-8000-00805f9b34fb`.

| UUID | Meaning | Lite interpretation |
| --- | --- | --- |
| `2a24` | Model number | `BB-V2-0-L` |
| `2a25` | Serial number | Wi-Fi device identifier: twelve hexadecimal characters without separators, rather than the printed manufacturing serial |
| `2a26` | Firmware revision | `3.17.5.13` |
| `2a27` | Hardware revision | `BB-V2-0-L` |
| `2a28` | Software revision | `3.17.5.13` |
| `2a29` | Manufacturer | `Empowered Homes Inc.` |

Do not assume the Lite's `2a25` interpretation applies to newer models. Their
cloud provisioning request distinguishes `deviceId` from `serialNumber`.
Browser access to Device Information characteristics can also be restricted;
clients should tolerate optional information reads failing.

### Wi-Fi configuration

Service: `4d790000-7361-5769-4669-436f6e666967`.
Each characteristic below uses the suffix `-7361-5769-4669-436f6e666967`.

| UUID prefix | Role | Properties on the Lite |
| --- | --- | --- |
| `4d790001` | Configuration commands | Write |
| `4d790002` | Configuration status | Read, Notify |
| `4d790003` | Identify | Read |
| `4d790000` | Unnamed characteristic sharing the service UUID | Read |

Status notifications use the first byte as the status code. The status
characteristic has a standard `2902` notification configuration descriptor.
An idle read returned `00`; its meaning is not assigned by the known status enum.

Identify is read as a little-endian 16-bit value. The observed bytes were
`ce fa` (`64206` unsigned); their semantic meaning remains unknown. The unnamed
characteristic returned `00` and has no identified command or control purpose.

### Wi-Fi network scanning

Service: `4d790000-7361-2053-5349-442b52535349`.
Each characteristic below uses the suffix `-7361-2053-5349-442b52535349`.

| UUID prefix | Role | Properties on the Lite |
| --- | --- | --- |
| `4d790001` | Network record | Read, Write |
| `4d790002` | Network count and scan trigger | Read, Write, Notify |

Only the scan-trigger write and record reads have an identified role. The
network-record Write property does not establish a command format. The count
characteristic also has a `2902` descriptor; notification support alone does not
establish a scan-completion signal.

## Wi-Fi provisioning transaction

Writes go to the configuration characteristic. Commands without a payload are
one byte. String commands use this format:

```text
[command:u8, length:u8, ASCII(Base64(UTF-8(value)))]
```

`length` counts the Base64 text bytes, excluding the two-byte header. It does
not count the original string's characters or UTF-8 bytes. There is no trailing
NUL or checksum in this packet format.

| Step | Write | Expected status |
| --- | --- | --- |
| Start configuration | `20` | `20` — ready |
| Send SSID | `21 <length> <encoded SSID>` | `21` — SSID received |
| Send password | `22 <length> <encoded password>` | `22` — password received |
| Finish configuration | `2f` | `40` — connecting, then `41` — connection established, or a failure |

For example, the SSID `Home` becomes Base64 `SG9tZQ==` and is sent as:

```text
21 08 53 47 39 74 5a 51 3d 3d
```

Wait for each matching acknowledgement before advancing. An ATT write response
only acknowledges the write; the Mysa status notification acknowledges the
protocol step. Notifications can arrive before the write call returns, so begin
tracking them before writing. A disconnect or timeout without `41` is not a
successful join. The browser implementation allows up to 90 seconds for joining;
that is a client timeout, not a documented firmware guarantee.

| Status | Meaning |
| --- | --- |
| `20` | Start configuration ready |
| `21` | SSID received |
| `22` | Password received |
| `40` | Connecting |
| `41` | Connection established |
| `42` | Connection failed |
| `80` | Timeout |
| `fd` | Invalid sequence |
| `ff` | Internal error |

All codes in the tables above are hexadecimal. `41` means the thermostat reports
joining Wi-Fi; it does not confirm internet access, Mysa account enrollment, or
HomeKit availability.

## Reading nearby networks

Write the single byte `01` to Network Count to request a fresh scan. Allow time
for results, read the count as a little-endian 16-bit integer, then read Network
Record repeatedly to obtain the records. The browser implementation waits two
seconds before reading the count and bounds the operation with timeouts; the
protocol does not provide an established ready notification in the examined flow.

Each record has this layout:

```text
[01, length:u8, ASCII(Base64(UTF-8(SSID))), RSSI:i8]
```

Here `length` includes both the Base64 text and the final RSSI byte, unlike the
Wi-Fi credential packet length. Total record length is therefore `length + 2`.
RSSI is a signed byte in dBm; for example, `d7` is −41 dBm. This measures the
Wi-Fi access point as seen by the thermostat, not the phone's Bluetooth signal.

The setup page combines duplicate network names using their strongest reading,
sorts by signal strength, and displays results without selecting or filling in
an SSID. Its Good/Weak/Poor colors are approximate Wi-Fi guidance, not
Mysa-certified operating thresholds or a connectivity test.

## Credential transfer on newer models

Baseboard V3, Central HVAC, and Climate Sensor add a credential-file transfer
to provisioning. The format below is recovered from app 4.20.1 and remains
**unverified on hardware**. This service was absent from the tested Lite.

| Role | UUID |
| --- | --- |
| File-transfer service | `ecaa050b-0da4-e394-d64c-5d08e47cb41d` |
| Control and status characteristic | `708aa387-184f-fca4-6b4e-eef896ec9bb5` |
| Data characteristic | `81838135-4ca1-96b1-1b47-6e96772fef72` |

The app obtains a Base64 `credentials` field from `POST /pairing/credentials`
using `{deviceId, model, serialNumber}`. It decodes that field into file bytes;
the data characteristic receives those bytes, not the Base64 text.

### Transfer format

The control command is six bytes:

```text
Start:  [01, 00, fileLength:u32LE]
Cancel: [02, 00, 00, 00, 00, 00]
```

The command enum also defines `00` as None. The app splits the file into chunks
of at most **256 bytes**, writes them sequentially to the data characteristic,
and waits **50 ms** between writes. These are app-level chunks and pacing;
they do not specify the negotiated ATT MTU or guarantee one radio packet per
chunk. No offset or sequence header is added to the file chunks at this layer.

Reading the control characteristic yields:

| Byte offset | Type | Field |
| --- | --- | --- |
| 0 | `u8` | Command |
| 1 | `u8` | Status: 0 = Idle, 1 = Downloading |
| 2 | `u32LE` | Expected file size |
| 6 | `u32LE` | Received byte count |
| 10 | `u32LE` | CRC-32 |

The app cancels an existing Downloading transfer before starting another. After
writing all chunks, it reads the status back and requires Idle, both byte counts
equal to the submitted file length, and a matching CRC-32. The checksum uses the
reflected polynomial `0xedb88320`, initial value `0xffffffff`, and final XOR
`0xffffffff`. The examined sequence uses status readback rather than a separate
end-of-file command.

### Cloud registration and completion

The broader setup flow also registers the device in its OTA fleet, claims it on
the Mysa account, verifies fresh cloud state, and calls `/pairing/finalize`.
See the [newer-model cloud setup sequence](cloud-workflows.md#baseboard-v3-central-hvac-and-climate-sensor)
for the endpoints and fleet names. The app's `REGISTER_DEVICE` and
`UPDATING_FIRMWARE` names are setup-state enum values, not additional bytes in
the Wi-Fi notification table above.

These models use the cloud-provided Matter setup code for subsequent Matter
pairing. Credential-file transfer is not evidence of a firmware-upload service,
and the file has not been established as a source of reusable MQTT credentials.

## What can be verified after provisioning

For HomeKit-capable models, local discovery uses `_hap._tcp.local.` over mDNS.
An advertisement establishes that the accessory is discoverable; bit 0 of its
`sf` field indicates that it is unpaired when set. A cleared bit reports an
existing pairing but does not identify the controller or prove that its saved
keys still work. A successful authenticated HomeKit read is the stronger check.
See the [HomeKit protocol reference](../custom_components/mysa_local/protocols/homekit/README.md).

The standalone setup webpage cannot perform ordinary mDNS discovery itself. A
Home Assistant backend or local helper could perform that check after BLE setup.
Missing discovery is inconclusive: multicast filtering, network separation, or
the accessory sleeping can hide it.

On the tested Lite, the known Bluetooth surface provides no identified setpoint,
temperature, mode, reboot, firmware-upload, or MQTT-credential operation. No BLE
command to enable a temporary Wi-Fi access point has been established either.
These limits describe the observed setup interface, not every possible firmware
mode. Runtime control remains through [HomeKit](../custom_components/mysa_local/protocols/homekit/README.md) or the
[HTTPS cloud protocol](../custom_components/mysa_local/protocols/cloud/README.md); the separate
[MQTT reference](mqtt.md) covers MQTT authentication and permissions.

## Temporary Wi-Fi access point setup

For Bluetooth setup, use the [protocol reference](bluetooth.md)
or [Wi-Fi setup page](https://sokolskynikita.github.io/mysa-wifi-setup/).
SoftAP is a separate local HTTP provisioning path:

The setup server is `http://192.168.4.1:57961`. Read `/pubkey`, `/hwid` and
`/model`, then send `GET /network/` with an RSA-encrypted JSON `{ssid,pass}`
value in the `mysa-data` header. Poll `/connected/` (200 joined, 202 pending,
404 failed), then call `/reboot/`.

The earlier client encoding JSON-quotes the ciphertext header and also calls
`/identify`. The later encoding removes line breaks and sends the ciphertext
unquoted. These are client-format variants; their firmware compatibility has
not been established here.

On two Lite units in normal Wi-Fi mode, port 57961 refused connections. The only open TCP port found was HomeKit on 57959. An unauthenticated HomeKit HTTP request returned 470. These observations do not establish a usable local REST control API.


The SoftAP server and its activation on a Lite have not been demonstrated. A
refused port in normal mode does not prove that the service exists in that
firmware. The RSA key read from `/pubkey` is a provisioning encryption key,
not an MQTT client certificate. The older app also contains NFC provisioning:
`GET /nfc/auth-key` supplies setup data and an NDEF text record carries
`ssid<RS>password<RS>`. This is app-derived and not an implemented setup-page path.

## Implemented coverage and remaining work

The separate Wi-Fi setup repository implements the legacy BLE transaction and
network listing, validated on Lite. It owns any browser implementation backlog.
Mysa Local contains no BLE runtime. Other legacy models require model-specific
validation; newer credential transfer, SoftAP activation/encoding compatibility,
and undocumented GATT properties remain unvalidated.

App evidence: 3.26.0, 3.54.1, and 4.20.1. The Lite GATT inspection on
October 11, 2026 exposed three services, twelve characteristics and eleven
readable values; an existing bond may have supplied security. No raw private
GATT values or Wi-Fi credentials are included here.
