# Cloud authentication

The [cloud protocol reference](../custom_components/mysa_local/protocols/cloud/README.md) owns endpoint availability, S/R/L terminology, observed limitations, and recommended client policy. This page supplies detailed wire mechanics; implementation status belongs to the [cloud tracker](implementation/cloud.md).

The protocols were reverse-engineered from the original Mysa apps. Hardware observations cover Baseboard Lite `BB-V2-0-L`, firmware `3.17.5.13`. Other-model behavior remains app/code-derived unless explicitly stated.

## Connection and authentication

### Service hosts

| Host | Use |
| --- | --- |
| `mysa-backend.mysa.cloud` | Current HTTPS API and migrated legacy REST routes |
| `app-prod.mysa.cloud` | Legacy REST host; still advertised as `regionApiBaseUrl`, with some push-registration and account routes |
| `cognito-idp.us-east-1.amazonaws.com` | Sign-in and token refresh |
| `i18n.mysa.cloud` | Static country and time-zone lists |
| `chunk.mysa.cloud/track` | Analytics |

Routing depends on feature flags: `MONOLITH_API_ACCESS` moves legacy routes to
`mysa-backend`, and `DISABLE_LEGACY_FALLBACK` disables retrying them on `app-prod`.
Both were enabled for the tested account. A host name alone therefore does not
identify the protocol family. Mysa Local's host selection is described in the
[cloud guide](../custom_components/mysa_local/protocols/cloud/README.md#how-it-is-organized).

### Cognito sign-in

Current and legacy clients share these public Cognito identifiers:

| Setting | Value |
| --- | --- |
| AWS region | `us-east-1` |
| User pool | `us-east-1_GUFWfhI7g` |
| App client | `6cktj934gasnc72f7jo2cmf6rt` |
| Identity pool | `us-east-1:e27f246e-ad23-41ce-ac80-eda86f8be424` |

The app client has no client secret. Cognito requests go to `https://cognito-idp.us-east-1.amazonaws.com/` with:

```http
X-Amz-Target: AWSCognitoIdentityProviderService.<Action>
Content-Type: application/x-amz-json-1.1
```

No AWS request signature is required for these user-pool calls. Sign-in uses `InitiateAuth` with `AuthFlow: "USER_SRP_AUTH"` and `AuthParameters` containing `USERNAME` and `SRP_A`. A `PASSWORD_VERIFIER` challenge follows. `RespondToAuthChallenge` sends `PASSWORD_CLAIM_SIGNATURE`, `PASSWORD_CLAIM_SECRET_BLOCK`, `TIMESTAMP` and `USERNAME` as `ChallengeResponses`, plus the challenge session if supplied. The response contains `IdToken`, `AccessToken`, `RefreshToken` and `ExpiresIn: 3600`.

The Python SRP implementation uses Cognito's 3072-bit group, SHA-256 and a 16-byte derived authentication key. Use the challenge's `USER_ID_FOR_SRP` when computing the response, even when sign-in began with an email address. Cognito also expects an English UTC timestamp with an unpadded day, for example `Fri Jan 2 03:04:05 UTC 2026`. The [SRP implementation](../custom_components/mysa_local/protocols/cloud/srp.py) contains the calculation.

API authorization headers, token handling and supported challenges are described
in the cloud guide: [authentication](../custom_components/mysa_local/protocols/cloud/README.md#using-the-client) and [request handling](../custom_components/mysa_local/protocols/cloud/README.md#errors-retries-and-integration-boundary).

Refresh uses `GetTokensFromRefreshToken` with:

[Cognito refresh request (credential placeholder only)](examples/cloud/cognito-refresh.request.json)

The observed refresh response supplied ID and access tokens with a one-hour
lifetime and no replacement refresh token. The Identity Pool is not needed for
HTTPS; the examined mobile app uses it for SNS push registration. Its credential exchange and
MQTT role are covered in the [MQTT connection model](mqtt.md#connection-and-authorization-model).

For HTTP errors and retries, see the [cloud error reference](../custom_components/mysa_local/protocols/cloud/README.md#errors-retries-and-integration-boundary).
The examined mobile app treats 409 as success generally; the Python client handles it specially
only for [device claims](cloud-workflows.md#claiming-an-older-model).

### Browser access

The tested APIs do not support ordinary cross-origin browser access:

| Service | Browser behavior |
| --- | --- |
| Cognito | Browser sign-in allowed |
| `mysa-backend` | Authorization/Content-Type preflights returned 404; actual responses also lacked `Access-Control-Allow-Origin` |
| `app-prod` | Legacy-route preflights allowed access, but actual responses lacked `Access-Control-Allow-Origin`; newer pairing, state and bootstrap routes returned 403 |

Successful Cognito sign-in therefore does not make Mysa's API responses readable
by a webpage. Cloud requests need a backend, such as Home Assistant, or an
extension with the appropriate host permissions.
