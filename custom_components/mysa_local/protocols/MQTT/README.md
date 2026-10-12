# Mysa MQTT investigation

**Evidence reviewed: October 10–11, 2026.** Hardware tests used two Mysa
Baseboard Lite thermostats (`BB-V2-0-L`, firmware `3.17.5.13`) on one Mysa
account. This directory documents the investigation; it contains no runtime
MQTT implementation.

Direct MQTT control was not established. Fresh credentials successfully opened
the broker connection and subscribed to both thermostats, but the tested
Cognito identity's attached policy allowed neither publishing commands nor
receiving messages. Publishing was rejected; passive MQTT received no
telemetry while HTTPS readings advanced. The earlier failure before CONNACK
was not reproduced, so its exact cause remains unresolved.

This is a finding about one Mysa-issued identity, not a claim that MQTT is
disabled for every Mysa user or every Lite thermostat. Mysa Local uses
[HomeKit](../homekit/README.md) for local communication and the
[HTTPS cloud protocol](../cloud/README.md) for cloud operations.

## Connection and authorization model

There are three distinct communication paths:

| Path | Transport | Evidence |
| --- | --- | --- |
| App 3.54.1 → Mysa cloud | Direct AWS IoT MQTT over secure WebSocket | Decompiled app client and command generators |
| App 4.20.1 → Mysa cloud | HTTPS reads and writes | Decompiled bundle and captured app traffic; no direct MQTT client found |
| Thermostat → Mysa cloud | TLS MQTT on port 8883 | Router traffic captures; separate from the mobile client's credentials |

The legacy client signs into Mysa's Cognito **User Pool**, obtains an ID token,
then exchanges it through the **Identity Pool** using `GetId` and
`GetCredentialsForIdentity`. The resulting temporary access key, secret key,
and session token sign the AWS IoT WebSocket request. Public pool identifiers
and the authentication exchange are recorded in the
[cloud authentication reference](../../../../docs/cloud-api.md#cognito-sign-in).

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

| AWS action | Checked when | Resource needed by this client | October 11 attached policy |
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

The October 11 retest found exactly one attached policy, `default`.
`GetPolicy(default)` returned:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": ["iot:Connect", "iot:Subscribe"],
      "Resource": "*"
    }
  ]
}
```

The wildcard covers resources for the two listed actions; it does not grant
other actions. The absence of `iot:Publish` and `iot:Receive` is therefore
material even though connection and subscription requests succeed.

### Test results

| Test | Observed result | What it establishes |
| --- | --- | --- |
| Original attempt, October 10 | TLS and WebSocket upgrade succeeded (`101`, subprotocol `mqtt`); socket closed after CONNECT without CONNACK | The transport opened, but the MQTT session was not established. No policy snapshot or definitive cause was recorded. |
| Fresh credentials and MQTT 3.1.1, October 11 | CONNACK `20 02 00 00` | MQTT connection accepted with the tested credentials and a random UUID client ID. |
| Separate QoS 1 subscriptions to both owned devices' `/out` topics | SUBACK granted QoS 1 for each | The broker accepted the subscriptions. |
| Official-app echo published to an owned device's `/in` topic | Broker disconnected the WebSocket | Publication failed before a device response could be assessed. |
| Same echo through SDK-signed AWS IoT HTTPS `Publish` | HTTP 403 `ForbiddenException` | Independent confirmation that publication was forbidden; this does not depend on the hand-built MQTT packets. |
| Passive subscriptions while Mysa's HTTPS live-mode route requested 20 seconds of reporting | Both HTTPS reading timestamps advanced; zero MQTT messages during 25 seconds of listening | The devices were reporting, but the MQTT client received no telemetry, consistent with the missing Receive permission. |
| Saved Paho diagnostic | Successful CONNACK and both SUBACKs; same limited policy | Connection and subscription results reproduced with an independent MQTT implementation. |

The AWS IoT HTTPS `Publish` test is distinct from Mysa's own HTTPS state API.
The former tests AWS IoT authorization; the latter remains the working cloud
path. The live-mode comparison used
[`POST /mysa-legacy/stream/home/{home}`](../../../../docs/cloud-api.md#live-mode-and-liveness)
and confirmed fresh readings through the state API.

No policy attachment was performed. Tests left thermostat setpoints and modes
unchanged; the temporary reporting request expired on its own. The connected
Android test phone was not needed for this retest.

## Investigated fixes and their limits

| Candidate | Investigation | Assessment |
| --- | --- | --- |
| Attach the policy as the old app did | App 3.54.1 calls `AttachPrincipalPolicy` with `policyName: "default"` and the Identity Pool ID. Its older MQTT client awaits this before connecting; its newer client relies on a sign-in attachment. The tested identity already had it. | The original missing-policy hypothesis was not confirmed. Reattaching the same document cannot add Publish or Receive. |
| Correct SigV4 signing or renew credentials | Retried with fresh identity credentials and a fresh UUID. The successful signer appends the encoded session token after calculating the signature. The earlier scratch signer and [Mysa_HA signer](https://github.com/kgelinas/Mysa_HA/blob/main/custom_components/mysa/mysa_auth.py) use the same ordering. | This established a working connection, but does not establish a signing bug as the cause of the historical failure. Credential refresh cannot grant absent actions. |
| Reproduce another old-app bootstrap step | Traced `getCognitoUserAttr` → `updateIoTCredentials`. The flow obtains identity credentials and attaches `default` directly through AWS IoT. No additional per-device policy attachment or identity-ID registration with Mysa's `/users` API was found in that flow. | No omitted app-side provisioning step was identified that would restore the missing permissions. |
| Change the Lite command payload | Compared model type, timestamps, command encoding, and response fields against the decompilation and community implementations. | These differences can explain accepted commands that a device ignores. They cannot resolve an AWS publish authorization denial, so setpoint experiments were not pursued. |
| Retry or change MQTT libraries | Used both the initial WebSocket probe and Paho; also tested signed AWS IoT HTTPS Publish. | The publish restriction is not explained by one MQTT framing implementation. Reconnect hardening may help transient failures but does not change this policy. |
| Obtain live updates through Mysa HTTPS | Requested a short reporting interval and observed advancing state timestamps for both devices. | Verified the existing cloud alternative; it does not restore direct MQTT delivery. |

## Community evidence and timeline

The following primary reports were reviewed on October 11, 2026. Dates are
in America/Los_Angeles; GitHub may display a different day in another time
zone. These are external reports, not additional hardware tests for this
integration. Their failure stages matter more than the shared word “MQTT.”

| Date | Source and observation | What the outcome establishes |
| --- | --- | --- |
| May 5–7, 2025 | [mysotherm #1](https://github.com/dlenski/mysotherm/issues/1): BB-V1/AC client closed while awaiting SUBACK. The maintainer mentioned server changes following his vulnerability disclosure, but his own access still worked. | The reporter confirmed a client update fixed this case. It provides security-change context, not evidence that Publish/Receive were removed. |
| November 5–8, 2025 | [mysa-js-sdk #178](https://github.com/bourquep/mysa-js-sdk/issues/178): connect/subscribe succeeded, then the status-request publish triggered a hangup. | [SDK 2.0.2](https://github.com/bourquep/mysa-js-sdk/releases/tag/v2.0.2) changed client-ID/reconnect handling; [mysa2mqtt 1.2.1](https://github.com/bourquep/mysa2mqtt/releases/tag/v1.2.1) incorporated it and the issue was declared fixed. A publish hangup alone does not prove a policy denial. |
| January 16–18, 2026 | [mysotherm #6](https://github.com/dlenski/mysotherm/issues/6): apparently acknowledged commands did not change the setpoint. The maintainer reported working V2 Lite control and described the app's duplicate command encodings. | A payload/device-processing problem after publication, unlike our 403. Also evidence against a universal Lite MQTT shutdown. |
| March 3, 2026 | [mysa2mqtt #125](https://github.com/bourquep/mysa2mqtt/issues/125): a five-Lite setup authenticated and discovered devices, but failed the WebSocket upgrade. | A connection-stage failure; no policy document was supplied. |
| March 14–April 9, 2026 | [Mysa_HA #11](https://github.com/kgelinas/Mysa_HA/issues/11): BB-V2 upgraded from Lite received HTTP 403 during MQTT connection while REST polling worked. | The maintainer marked it fixed March 22, but the reporter still reproduced the 403 on version 0.9.2 on April 9. Closure did not demonstrate restored access for that setup. |
| July 18–21, 2026 | A [follow-up to #125](https://github.com/bourquep/mysa2mqtt/issues/125#issuecomment-5013491501) described INF connect/subscribe succeeding and the first status publish disconnecting; Lites lacked live updates. | [PR #208](https://github.com/bourquep/mysa2mqtt/pull/208) closed #125 by adding REST polling. That restores readings through HTTPS, not MQTT permissions. |
| July 22, 2026 | [Mysa_HA #27](https://github.com/kgelinas/Mysa_HA/issues/27): AC telemetry/keepalive worked, but commands and status requests triggered disconnects. | A related publish-stage symptom with no policy evidence or documented fix. Its working reception differs from our limited policy. |
| October 10–11, 2026 | [mysa2mqtt #299](https://github.com/bourquep/mysa2mqtt/issues/299): BB-V1 connect/subscribe worked, but every publish disconnected, including with an independent Python AWS IoT client. Mysa HTTPS state writes worked. | The [maintainer still had working BB-V1 devices](https://github.com/bourquep/mysa2mqtt/issues/299#issuecomment-6109575434) and proposed REST fallback. No permission-restoring fix was documented at inspection. |
| October 11, 2026 | [Local policy inspection and retest](#live-evidence) | Direct authorization evidence for the tested identity, beyond the symptom reports above. |

The projects have solved some client and payload problems and provided HTTPS
fallbacks. None of the inspected fixes demonstrated how to restore missing
Publish/Receive grants for an identity in the state observed here.

## Likely permission changes: what, when, and why

**Working hypothesis:** legacy direct messaging access is retained for some
Mysa identities but restricted or incompletely provisioned for others, during
the move toward HTTPS-backed app control. Reports of different outcomes are
consistent with this, but do not isolate permissions from client or firmware
differences. Confidence in this historical explanation is low. The inspected
policy establishes the October 11 restriction; it does not prove that this
identity previously had broader rights.

### What may have changed

| Possible AWS change | Fit with the evidence | Limit |
| --- | --- | --- |
| Remove Publish/Receive from a version of the shared `default` policy | Would produce the exact action split observed here. | No older policy document was captured. We cannot assert that `default` ever granted those actions. |
| Stop attaching, or remove, an additional device-scoped policy | Would leave only the baseline `default`; older identities with additional grants could still work. | No such supplemental policy or provisioning flow was found in the inspected app. This remains a hypothesis. |
| Change the authenticated IAM role, its policies, or Cognito role mapping | Could restrict subsets of identities even when their IoT policy allows an action. | The IAM layer was not audited. The observed IoT-policy omission already explains the missing grants without requiring this additional change. |

AWS named IoT policies are shared, versioned documents, not private copies
per Mysa user. Changing `default` would affect identities referencing that
version; continuing access could depend on other grants or different identity
configuration. AWS also distinguishes implicit denial from an explicit Deny:
our snapshot contains missing Allows, not a Deny statement. See
[AWS IoT policy structure and evaluation](https://docs.aws.amazon.com/iot/latest/developerguide/iot-policies.html).

### Approximate timing

The defensible estimate is a **period of uneven legacy compatibility across
2025–2026, with restricted Publish/Receive confirmed by October 11, 2026**,
not a single demonstrated cutoff. Similar publish-disconnect complaints
existed by November 2025, but that case had a client-side fix. The unresolved
July and October 2026 publish reports are compatible with an authorization
change affecting some identities by mid-to-late 2026; they do not prove a
common cause or an AWS deployment date. Working Lite control in January and
working BB-V1 control in October argue against a universal model-wide cutoff.

It is also possible that this identity never received the additional rights
needed by old clients. Dating the restriction from account age or the first
complaint would therefore overstate the evidence.

### Plausible reason

The strongest architectural explanation is that moving app control behind
Mysa's HTTPS backend reduced the need to grant mobile-user identities direct
device-topic Publish/Receive access. That would let the backend mediate
ownership checks and command handling. The old and new app architectures
support this inference, but do not establish whether permission removal was
intentional. The May 2025 maintainer report supplies a possible security
hardening context, not a demonstrated link to this policy. Incomplete legacy
provisioning or a migration regression remains an alternative explanation.
There is no evidence here of a deliberate third-party ban or a Lite-only
commercial restriction.

An actual change date would require retained policy documents and deployment
or attachment/role history. AWS provides
[`ListPolicyVersions`](https://docs.aws.amazon.com/iot/latest/apireference/API_ListPolicyVersions.html)
and [`GetPolicyVersion`](https://docs.aws.amazon.com/iot/latest/apireference/API_GetPolicyVersion.html)
for authorized inspection; those histories were not obtained. A version's
creation date alone would not show when it became effective for this identity.

## Legacy wire protocol

The following is the app 3.54.1 reference recovered from static analysis. It
is retained for a future authorized test; the investigation did not validate
MQTT setpoint control on the two test devices.

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

```json
{
  "ver": "1.0",
  "src": {"type": 100, "ref": "<user-pool-sub>"},
  "dest": {"type": 1, "ref": "<device-id>"},
  "time": 1791700000,
  "msg": 44,
  "id": 1791700000000,
  "body": {
    "ver": 1,
    "type": 5,
    "cmd": [{"tm": -1, "sp": 21.5}]
  }
}
```

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

[`tools/mqtt_probe.py`](../../../../tools/mqtt_probe.py) is a standalone,
passive diagnostic. It reuses `protocols.cloud.MysaAuth` and `MysaClient`,
obtains Identity Pool credentials, inspects attached policies, and checks
MQTT CONNECT and per-device SUBACK through Paho. It discovers device IDs from
the authenticated account rather than accepting arbitrary topic targets.

From the repository root, use a separate environment with `aiohttp`, `boto3`,
and `paho-mqtt>=2`, then run:

```sh
python tools/mqtt_probe.py --credentials /private/path/mysa.json --duration 20
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
or publishes. The earlier echo, AWS IoT HTTPS Publish, and live-mode comparison
were separate controlled experiments; rerunning this tool does not repeat them.

## Validation criteria for a future fix

If access changes, a meaningful validation should first confirm delivery of
the safe echo and fresh telemetry, then test a controlled state change and
read it back from the device. CONNACK, SUBACK, or a broker-level publish
acknowledgement alone must not be reported as successful thermostat control.

Source provenance and the Android decompilation/capture process are documented
under [Investigation method](../../../../docs/cloud-api.md#investigation-method).
