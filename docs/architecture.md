# Integration architecture

Mysa Local is one Home Assistant integration with three independent device
protocols. HomeKit is the preferred path for thermostat readings and control on
the LAN. HTTPS provides cloud settings, account features and fallback control;
MQTT adds live, read-only telemetry when account access is enabled.

```mermaid
flowchart TD
    E[Home Assistant entities, services, and config flow]
    T[Thermostat and command router]
    HA[Home Assistant adapters and coordinator]
    HK[HomeKit protocol package]
    CL[Cloud protocol package]
    MQ[Optional MQTT protocol package]
    DEV[Mysa thermostat on the LAN]
    API[Mysa HTTPS API]
    IOT[Mysa AWS IoT endpoint]

    E --> T
    T --> HA
    HA --> HK
    HA --> CL
    HA --> MQ
    HK -->|HAP over IP| DEV
    CL -->|HTTPS| API
    MQ -->|Secure WebSocket MQTT| IOT
```

The protocol packages do not call each other. Home Assistant code coordinates
them through semantic device operations and state.

## Protocol implementations

The [`mysa-client` package](https://github.com/SokolskyNikita/mysa-client)
implements the cloud API, HomeKit over IP, and an optional MQTT client. It owns
Cognito authentication, HTTP requests, response parsing and command encoding,
HomeKit characteristic parsing, MQTT connection and message parsing, and
protocol errors. Its public APIs have no Home Assistant dependency. MQTT stays
opt-in for standalone consumers; Mysa Local explicitly opts in when Mysa
account access and cloud use are enabled, its global **Live MQTT readings**
option is on, and each selected thermostat's MQTT setting is on. The integration
passes the client only the enabled account-device allowlist. See the package's [cloud guide](https://github.com/SokolskyNikita/mysa-client/blob/main/src/mysa_client/cloud/README.md),
[HomeKit guide](https://github.com/SokolskyNikita/mysa-client/blob/main/src/mysa_client/homekit/README.md),
and [MQTT guide](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/mqtt.md)
for the APIs, lifecycle, timeouts, and failure handling. The [HA MQTT guide](mqtt.md)
covers the integration options, value selection, and hardware evidence.

Home Assistant installs the library as an external dependency. The integration
adapters own the caller-provided HTTP session and discovery/controller
lifecycle, durable token and pairing storage, polling, retries, state freshness,
and command confirmation. Protocol imports use `mysa_client.cloud`,
`mysa_client.homekit`, and `mysa_client.mqtt`.
The protocol boundary tests verify that the library and integration adapters
remain separated.

## Home Assistant integration layer

The integration layer owns lifecycle, policy, persistence, and presentation:

- `homekit_adapter.py` connects Home Assistant discovery, controller, and
  pairing lifecycle to the HomeKit protocol package.
- `local_adapter.py` adapts the HomeKit device to Home Assistant polling,
  subscriptions, availability, and shutdown.
- `cloud_coordinator.py` schedules cloud refreshes, retries, and cloud
  operations. It calls the cloud protocol client and publishes typed state to
  the integration.
- The account runtime owns one MQTT session for account thermostats selected by
  the global and per-thermostat options. It manages stream requests and
  per-field receipt-time freshness for read-only humidity and numeric duty
  cycle. See the [MQTT guide](mqtt.md) for selection, fallback, and hardware
  evidence.
- `thermostat.py` combines the three state sources and exposes semantic
  operations to entities and services.
- `command_router.py` serializes writes and handles local-first fallback and
  reconciliation when a delayed local write completes after a newer command.
- `pairing_store.py` persists pairing credentials and recovery state using HA
  storage. The HomeKit pairing flow receives pairing data and calls the HA
  persistence callback as soon as the accessory accepts a pairing, before any
  further network read.
- `config_flow.py` owns initial setup and HomeKit discovery, and registers the
  options and thermostat subentry handlers from `flows/`.
- `schedule.py` and platform entities implement schedule workflows and the
  Home Assistant user interface.

Entities should ask the thermostat layer to set brightness, a temperature
limit, a button lock, or another device setting. They should not construct
cloud request dictionaries or refer to HomeKit characteristic identifiers.
Protocol-specific request formatting belongs in the matching protocol
package; command ordering and source selection belong in the integration.

Cloud response models and enums are public types from
`mysa_client.cloud`, and the integration reads them for state and capability
display. They describe the cloud's data shape. A future domain-model layer
could make the integration independent of those response models, but that is a
separate refactor from separating the two transports.

### Setup and management flows

The flow modules separate the three entry points while sharing thermostat
operations:

- `config_flow.py` assembles the initial entry, including optional cloud
  sign-in and the queues of thermostats to pair or add to the account.
- `flows/subentry.py` adds and reconfigures individual thermostats, including
  names, account membership, local pairing, and heater settings.
- `flows/options.py` manages integration settings, the account overview, and
  sign-in/sign-out. `flows/account.py` owns shared sign-in schemas, credential
  handling, and the reauthentication steps inherited by the initial flow.
- `flows/thermostat.py` provides discovery, pairing recovery, HomeKit Device
  takeover, and account-linking steps reused by setup, subentries, and repairs.
- `flows/state.py` defines `ThermostatFlowState`. Each flow initializes its own
  instance through `ThermostatSteps.__init__`, so selections, pairing sessions,
  discovery maps, pending recoveries, and account choices have explicit defaults
  and cannot leak between flows. Initial-setup queues and pending subentries
  remain on the flow that owns them.
- `flows/shared.py` contains presentation, PIN validation, and entry helpers.
  Runtime startup imports its pairing-ID helper directly without loading the
  config-flow registration module.

Tests follow the same boundaries: initial setup in `test_config_flow.py`,
thermostat management in `test_subentry_flow.py`, options and authentication in
`test_options_flow.py`, and shared thermostat steps in `test_thermostat_flow.py`.
Account issues and their repair flows are covered by `test_repairs.py`.

## Testing the boundaries

Protocol tests exercise cloud authentication, parsing, and command encoding
with fixtures and mocked HTTP responses. HomeKit protocol tests use the
`aiohomekit` test accessory and verify that protocol identifiers and status
handling are translated to semantic fields. Neither protocol test suite needs
to contact a real Mysa device.

Home Assistant tests use fake cloud responses, HomeKit accessories, and MQTT
telemetry to check setup, entity behavior, source fallback, write ordering, and
recovery. They should interact with the adapters or semantic device operations
rather than mutating protocol adapter internals.

Live-device checks are useful for confirming undocumented cloud payloads and
HomeKit behavior across firmware models. Keep them separate from routine test
runs: use reversible settings, restore the original values, and avoid pairing
or removal operations unless the test specifically requires them.

## Cloud protocol findings versus implementation

The [cloud tracker](implementation/cloud.md) owns the detailed implementation
work list. Cloud acquisition now reads structured and legacy state independently, preserving
per-field observations and source health. `cloud_state.py` selects values and
tracks pending commands separately from confirmed reports. The client provides
allowlisted host fallback and native schedule snapshots; the schedule journal
retains origin during clearing and rollback. Hardware confirmation remains
separate from code/fixture coverage.

Run `python3 tools/check_docs.py` for offline documentation checks. It does not
import the integration, obtain credentials, or contact any service or thermostat.
