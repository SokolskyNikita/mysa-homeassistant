# Mysa cloud protocol

This package implements the Mysa account and thermostat cloud API used by the
integration. It owns Cognito authentication, HTTPS requests, cloud response
models, Mysa-specific enums, and the translation between semantic commands and
the API's JSON fields. Home Assistant policy and HomeKit communication live in
the surrounding integration; this package does not choose which transport to
use for a thermostat.

## Using the client

The main entry points are `MysaAuth` and `MysaClient`, exported from
[`__init__.py`](__init__.py). The caller supplies and owns an `aiohttp` session.
The cloud-facing `device_id` should be taken from the account's device data.

```python
import aiohttp

from custom_components.mysa_local.protocols.cloud import MysaAuth, MysaClient, Mode


async def set_heat(refresh_token: str, device_id: str, user_agent: str) -> None:
    async with aiohttp.ClientSession() as session:
        auth = MysaAuth(
            session,
            refresh_token=refresh_token,
            user_agent=user_agent,
        )
        client = MysaClient(session, auth, user_agent=user_agent)
        await client.set_temperature(device_id, Mode.HEAT, 21.5)
```

This example changes the heating setpoint; it does not change the operating
mode. In HA, use the integration's shared session rather than creating one per
call.

`MysaAuth.sign_in(email, password)` can be used when starting without a refresh
token. Its SRP exchange derives the Cognito password proof locally; the
password itself is not sent as a request field. The auth object keeps the ID
token in memory and exposes the refresh token so the caller can persist it.
Its optional `on_refresh_token` callback reports replacement refresh tokens;
omitted replacement tokens leave the existing token intact. This package does
not own credential storage.

The client offers reads for bootstrap/account data, devices, state,
capabilities, usage, history, firmware, holds, schedules, and shortcuts. It
also offers semantic writes such as `set_temperature`, `set_mode`,
`set_fan_speed`, `set_swing`, `set_button_lock`, `set_display_brightness`,
`set_setpoint_limit`, `set_temperature_offset`, `set_display_unit`, and
`set_heater_type`. Low-level operations such as `update_state` and
`update_settings` remain available for less common or advanced calls.

## How it is organized

- [`auth.py`](auth.py) implements the Cognito sign-in and token lifecycle. It
  refreshes ID tokens shortly before expiry and serializes concurrent refreshes.
- [`srp.py`](srp.py) contains the Cognito Secure Remote Password calculations;
  it has no HTTP or Home Assistant responsibilities.
- [`client.py`](client.py) owns HTTP transport and endpoint request/response
  handling. The primary API host is `mysa-backend.mysa.cloud`; the older host is
  used for `replace_device()`'s purge-and-add operation.
- [`commands.py`](commands.py) converts semantic operations into Mysa state or
  settings fields. It also builds schedule event state and weekday encodings.
- [`models.py`](models.py) parses cloud JSON into typed records such as
  `DeviceState`, `DeviceRecord`, `Capabilities`, and `UsageDay`. Models retain
  the original JSON in `raw`; absent or incorrectly typed values generally
  become `None` rather than failing the entire parse.
- [`const.py`](const.py) defines API hosts, request/auth constants, and cloud
  enums. [`exceptions.py`](exceptions.py) defines the client error hierarchy.

## Requests and state

The usual sequence is authentication, account/device discovery, then state
reads and commands:

| Operation | Wire request |
| --- | --- |
| Bootstrap/account data | `GET /mobile/bootstrap` with the app version from `const.py` |
| Device records | `GET /devices` |
| State read | `GET /state/{device_id}`, or `POST /state/batch` with `deviceIds` |
| State change | `POST /state/{device_id}/update` with section changes and `source: 3` |
| Settings change | `POST /devices/{device_id}/` with settings fields; the trailing slash matters |

For example, `set_temperature(id, Mode.HEAT, 21.5)` encodes
`{"source": 3, "targetHeat": {"setpoint": 21.5}}`. Selecting cooling targets
`targetCool` instead. A mode change uses `modes.mode`; a display-unit change
uses the settings record's `Format` field. These wire differences belong in
`commands.py`, so HA entities do not construct them. Schedule encoding also
lives there: one event per weekday, Sunday numbered `"0"`, local execution
times as `HH:MM:00`, and the same semantic state sections. Schedule replacement
and recovery policy remain in the integration.

For state sections that contain `desired` and `reported`, `DeviceState` reads
the `reported` values. A successful HTTP write only means the API accepted the
request; it does not prove that the thermostat applied the change or that a new
telemetry reading arrived. Telemetry is parsed separately from
`latestTelemetry`. The Home Assistant coordinator may show a just-accepted
write optimistically, then re-read state to confirm or correct it.

## Errors, retries, and integration boundary

Requests use the caller's shared `aiohttp.ClientSession` and a 20-second total
timeout. The client sends the raw Cognito ID token in `authorization` (no
`Bearer` prefix), along with the configured user agent. A 401 triggers one
forced token refresh and one retry of that request. There is no general retry
loop: 429 responses raise `MysaRateLimitError` with a numeric `Retry-After`
hint when available; timeouts, transport failures, and server errors raise
`MysaConnectionError`. A 404 raises `MysaNotFoundError`; a remaining 401 or a
403 raises `MysaApiError`, not an account reauthentication signal. Cognito
authentication failures have their own `MysaAuthError` hierarchy. This
implementation supports the password-verifier SRP challenge; an unrecognized
challenge, including an unsupported multi-step challenge, raises
`MysaUnsupportedChallengeError`.

The integration layer translates these errors and applies its own policy.
[`cloud_coordinator.py`](../../cloud_coordinator.py) schedules polls, handles
backoff, serializes and spaces writes, caches accepted commands, and schedules
confirmation reads. [`thermostat.py`](../../thermostat.py) and
[`command_router.py`](../../command_router.py) choose between the cloud and
HomeKit paths, enforce local-first routing, and reconcile late local writes.
Telemetry freshness and entity availability are integration-level policy, not
client guarantees. See [the architecture overview](../../../../docs/architecture.md)
for the surrounding layers.

The protocol files themselves depend on the Python standard library and
`aiohttp`, not Home Assistant or HomeKit. They are bundled inside the HACS
integration rather than published as a separate distribution; importing them
through `custom_components.mysa_local` still initializes the Home Assistant
integration package.

## Scope and tests

This is a reverse-engineered implementation of the Mysa mobile API, not a
vendor-published SDK or a guarantee that undocumented endpoints remain stable.
Typed parsing and passing fixture tests establish code behavior, not support
for every Mysa model or firmware. The broader endpoint notes and evidence are
in [`docs/cloud-api.md`](../../../../docs/cloud-api.md).

Cloud request and response behavior is tested with mocked HTTP responses in
[`tests/api/test_client.py`](../../../../tests/api/test_client.py); SRP math is
checked in [`tests/api/test_srp.py`](../../../../tests/api/test_srp.py). These
tests do not contact real Mysa devices.
