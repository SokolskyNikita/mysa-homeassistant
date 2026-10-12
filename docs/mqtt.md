# Legacy Mysa MQTT protocol

**Evidence reviewed: October 10–11, 2026.** Hardware tests used two Mysa
Baseboard Lite thermostats (`BB-V2-0-L`, firmware `3.17.5.13`) on one Mysa
account. Mysa Local has no runtime MQTT implementation.

Direct MQTT control was not established. Fresh credentials successfully opened
the broker connection and subscribed to both thermostats, but the tested
Cognito identity's attached policy allowed neither publishing commands nor
receiving messages. Publishing was rejected; passive MQTT received no
telemetry while HTTPS readings advanced. The earlier failure before CONNACK
was not reproduced, so its exact cause remains unresolved.

This is a finding about one Mysa-issued identity, not a claim that MQTT is
disabled for every Mysa user or every Lite thermostat. Mysa Local uses
[HomeKit](../custom_components/mysa_local/protocols/homekit/README.md) for local communication and the
[HTTPS cloud protocol](../custom_components/mysa_local/protocols/cloud/README.md) for cloud operations.

## Connection and authorization model

There are three distinct communication paths:

| Path | Transport | Evidence |
| --- | --- | --- |
| Legacy app → Mysa cloud | Direct AWS IoT MQTT over secure WebSocket | Reverse-engineered from the original Mysa apps |
| Current app → Mysa cloud | HTTPS reads and writes | Reverse-engineered from the original Mysa apps; no direct MQTT client found |
| Thermostat → Mysa cloud | TLS MQTT on port 8883 | Observed device traffic; separate from the mobile client's credentials |

The legacy client signs into Mysa's Cognito **User Pool**, obtains an ID token,
then exchanges it through the **Identity Pool** using `GetId` and
`GetCredentialsForIdentity`. The resulting temporary access key, secret key,
and session token sign the AWS IoT WebSocket request. Public pool identifiers
and the authentication exchange are recorded in the
[cloud authentication reference](cloud-auth.md#cognito-sign-in).

The resulting AWS identity belongs to **Mysa's AWS infrastructure**. It
represents the user's Mysa login; it is not an AWS account owned or administered
by the thermostat owner. Keep these identifiers separate:

| Identifier | Purpose |
| --- | --- |
| User Pool `sub` | Mysa user identity; `src.ref` in app command envelopes |
| Identity Pool `IdentityId` | Principal to which AWS IoT policies are attached |
| Device `Id` from the account API | Topic suffix and command destination |
| Fresh UUID client ID | Identifies one MQTT connection |

### AWS permission controls

For authenticated Cognito identities, permissions must be present in both the
Identity Pool's authenticated IAM role and the IoT policy attached to the
identity. A successful Cognito login supplies credentials, not unrestricted
broker access. See [AWS's Cognito authorization model](https://docs.aws.amazon.com/iot/latest/developerguide/cog-iot-policies.html).

The legacy client exercises four independent MQTT data-plane permissions:

| AWS action | Checked when | Resource needed by this client | Observed attached policy |
| --- | --- | --- | --- |
| `iot:Connect` | MQTT CONNECT | `client/<clientId>` | Allowed |
| `iot:Subscribe` | MQTT SUBSCRIBE | `topicfilter//v1/dev/<deviceId>/out` | Allowed |
| `iot:Publish` | Each outgoing PUBLISH | `topic//v1/dev/<deviceId>/in` | Absent |
| `iot:Receive` | Each incoming message delivery | `topic//v1/dev/<deviceId>/out` | Absent |

These are resource suffixes under
`arn:aws:iot:us-east-1:<Mysa-AWS-account-id>:`. The doubled slash is intentional:
the topic starts with `/`. Subscribe uses a **topic filter** resource; Publish
and Receive use **topic** resources. See AWS's
[action definitions](https://docs.aws.amazon.com/iot/latest/developerguide/iot-policy-actions.html)
and [resource mapping](https://docs.aws.amazon.com/iot/latest/developerguide/iot-action-resources.html).
This explains why a successful SUBACK can coexist with no delivered messages.

Restoring this path would require Mysa to grant Publish and Receive for the
owned devices, with compatible IAM permissions. An attachment operation such
as [`AttachPolicy`](https://docs.aws.amazon.com/iot/latest/apireference/API_AttachPolicy.html)
associates an existing policy with an identity; it does not expand that policy.
These are controls in Mysa's AWS deployment, not settings in the thermostat
owner's personal AWS account.

## Live evidence

### Policy inspection

The tested identity had exactly one attached policy, `default`.
`GetPolicy(default)` returned:

[Policy document projection](examples/cloud/iot-policy.limited.json).

The wildcard covers resources for the two listed actions; it does not grant
other actions. The absence of `iot:Publish` and `iot:Receive` is therefore
material even though connection and subscription requests succeed.

### Test results

| Check | Observed result | What it establishes |
| --- | --- | --- |
| MQTT 3.1.1 connection | CONNACK `20 02 00 00` | Connection accepted with fresh credentials and a UUID client ID. |
| QoS 1 subscriptions to both devices' `/out` topics | SUBACK granted QoS 1 | Subscriptions accepted, independently reproduced with Paho. |
| Publish to a device's `/in` topic | WebSocket disconnected; AWS IoT HTTPS `Publish` returned 403 `ForbiddenException` | Command publication was forbidden before a device response could be assessed. |
| Passive subscriptions during 20 seconds of HTTPS live reporting | HTTPS readings advanced; no MQTT messages in a 25-second window | Consistent with the missing Receive permission. |

AWS IoT HTTPS `Publish` and Mysa's HTTPS state API are separate paths; the
latter remains usable. See [live-mode and liveness](cloud-state.md#live-mode-and-liveness).
An earlier connection failed before CONNACK; its cause remains unresolved and
does not change the later authorization findings.

## Legacy wire protocol

The following wire protocol was reverse-engineered from the original Mysa apps.
MQTT setpoint control remains unverified on the tested devices.

### Session and topics

| Property | App behavior |
| --- | --- |
| Endpoint | `wss://a3q27gia9qg3zy-ats.iot.us-east-1.amazonaws.com/mqtt` |
| Authentication | SigV4, service `iotdevicegateway`, region `us-east-1`; temporary Identity Pool credentials |
| MQTT session | MQTT 3.1.1, fresh UUID client ID, 60-second keepalive |
| Commands | Publish `/v1/dev/{deviceId}/in`, normally QoS 1, not retained |
| Readings | Subscribe `/v1/dev/{deviceId}/out`, QoS 1; no `/batch` subscription in the app |
| Device identifier | Account API `Id`, used verbatim |
| Publish pacing | One queued message per 350 ms |

IoT policy attachment uses the AWS IoT **control plane**, signed for service
`iot`, rather than the `iotdevicegateway` service used by the WebSocket URL.

### Lite commands and responses

The Lite always uses MCM `msg: 44` with `body.type: 5`. Other models take this
path from firmware 3.11.0 for V1, V2 and In-floor, or 3.12.0 for AC. A Lite
setpoint command has this shape; identifiers and times are illustrative:

[Array command](examples/mqtt/thermostat-command.array.json) and
[JSON-string command](examples/mqtt/thermostat-command.string.json).

`time` is Unix seconds; `id` is milliseconds. The app sends the command twice:
first with `cmd` as an array, then with that array encoded as a JSON string.
Its newer MQTT client adds a seconds-based `Timestamp`; the generator does
not set `resp`. Other device types are 1 for baseboard V1, 2 for AC, 3 for
In-floor, and 4 for baseboard V2.

| Command field | Meaning |
| --- | --- |
| `sp` | Setpoint in Celsius |
| `md` | Legacy mode: 1 off, 3 heat; distinct from the HTTPS mode enum |
| `ho` | 1 follow schedule, 2 hold |
| `tm` | Execution time in Unix seconds; `-1` means now |
| `br` | Brightness object |

A timed hold contains a hold-now entry and a future follow-schedule entry.
The app does not add `lk`, `stpt`, or `a_sp` to its Lite commands. The audited
Mysa_HA implementation differs by adding `resp: 2`, `stpt`/`a_sp`, and sending
one command representation. Those are compatibility questions, not established
causes of the observed authorization failure.

Device acknowledgements correlate the request through `resp_id` and carry
state in `body.state`, including `md`, `sp`, `ho`, `tr`, `fn`, and `ss`. The
app parser does not inspect `success`, `trig_src`, `br`, or `lk`. Live Lite
telemetry uses MCM `msg: 40`, with `body.ambTemp`, `hum`, `stpt`, and `dtyCycle`.

### Liveness and notifications

| Purpose | Legacy message fields in addition to `Device` and seconds-based `Timestamp` |
| --- | --- |
| Echo | `MsgType: 6`, `EventType: 4294967295`, `EchoID: 418`; expected reply is `MsgType: 5` with the same echo ID |
| Request live reporting | `MsgType: 11`, `Timeout: 180`; app renews every 150 seconds while foregrounded |
| Settings changed | `MsgType: 6`, `EventType: 0`, after the REST settings write |
| Schedule changed | `MsgType: 6`, `EventType: 1`, after the REST schedule write |

Legacy `MsgType` and MCM `msg` are different namespaces. In particular,
legacy `MsgType: 7` is named REBOOT in the app enum, and legacy `MsgType: 40`
is not MCM `msg: 40` telemetry. Neither is a suitable liveness probe. The
legacy source enum defines 0 as device manual and 1 as device schedule.

## Reproducing the diagnostic

[`tools/mqtt_probe.py`](../tools/mqtt_probe.py) is a standalone,
passive diagnostic. It reuses `protocols.cloud.MysaAuth` and `MysaClient`,
obtains Identity Pool credentials, inspects attached policies, and checks
MQTT CONNECT and per-device SUBACK through Paho. It discovers device IDs from
the authenticated account rather than accepting arbitrary topic targets.

From the repository root, use a separate environment with `aiohttp`, `boto3`,
and `paho-mqtt>=2`, then run:

```sh
python tools/mqtt_probe.py --credentials /path/to/credentials.json --duration 20
```

The private JSON accepts `refresh_token` and/or `email` plus `password`.
Omit `--credentials` to supply it through stdin. Keep credentials out of Git
and command-line arguments. The tool reports selected policy and connection
fields without tokens, signed URLs, or message payloads.

| Result | Interpretation |
| --- | --- |
| Exit 1 | The diagnostic failed or did not establish all expected subscriptions. |
| Exit 2, `authorization: "blocked"` | CONNECT/SUBACK completed, and inspection matched the exact limited policy shown above. |
| Exit 2, `authorization: "inconclusive"` | CONNECT/SUBACK completed, but policy inspection did not establish that exact restriction. This is not evidence that Publish/Receive work. |

The tool is not an IAM evaluator: other policies, different document shapes,
or denied inspection are reported as inconclusive. It never attaches a policy
or publishes, and it does not request live reporting.

## Validation criteria for a future fix

If access changes, a meaningful validation should first confirm delivery of
the safe echo and fresh telemetry, then test a controlled state change and
read it back from the device. CONNACK, SUBACK, or a broker-level publish
acknowledgement alone must not be reported as successful thermostat control.

For complementary HTTPS endpoints and model-specific findings, see the
[cloud API supplementary reference](README.md).

## Sources and unresolved questions

The original Android apps use MQTT in 3.26.0/3.54.1 and HTTPS in 4.20.1. The older 3.26.0 Lite MSG44 path requires firmware 3.13.0;
the later legacy app removes that gate. Both test devices exceeded it.

The missing-policy hypothesis is superseded for the inspected identity: `default`
was already attached. No extra per-device attachment or identity registration
step was found in the inspected sign-in path. Refreshing credentials, changing
payloads, or reattaching that same policy cannot supply its absent grants.
Why or when permissions differed remains unknown; community reports had different
failure stages and do not establish one universal cutoff. No direct-MQTT runtime
project is selected. Useful further validation requires new authorization evidence.

The later port-8883 credential attempt failed during TLS; see
[device connectivity](firmware-and-connectivity.md#time-transport-and-validation-limits).
There is no demonstrated thermostat-authenticated client here.
