# Standalone Mysa client extraction plan

## Objective

Extract the Mysa protocol implementations into a reusable Python library, then
make this Home Assistant integration its first consumer. Other applications
should be able to authenticate, discover devices, read state, and issue commands
without installing Home Assistant.

Keep the first extraction behavior-preserving. A unified client that chooses
between local and cloud connections is a possible follow-up, not part of this
initial migration.

## Implementation status (2026-10-12)

The extraction is complete in the sibling `mysa-client` repository:
`src/mysa_client/cloud` and `src/mysa_client/homekit` are the single maintained
protocol implementation. The client targets Python 3.11+, preserves the MIT
license and existing API, and offers HomeKit through the `homekit` extra.
The integration now imports `mysa_client` and retains all HA lifecycle and
state-confirmation policy.

Protocol tests and sanitized reference material have moved to the client.
The follow-up documentation audit also moved HomeKit, BLE/SoftAP, MQTT, Matter,
firmware/connectivity research, MQTT examples, and standalone diagnostic tools.
The [client documentation index](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/README.md)
owns protocol details and raw API coverage; the [HA documentation index](docs/README.md)
owns integration behavior and management coverage. Previously published protocol
pages remain as forwarding links, without duplicated explanations.
Both repositories have independent checks; the client also builds and inspects
wheel/source archives and tests Python 3.11–3.14 in CI. The integration's setup
script accepts the sibling wheel or editable checkout for local development.

The integration pins the published `mysa-client[homekit]==0.1.0` release:
[PyPI package](https://pypi.org/project/mysa-client/0.1.0/) and
[source repository and release](https://github.com/SokolskyNikita/mysa-client/releases/tag/v0.1.0).
PyPI Trusted Publishing is configured for the client's release workflow.

Validation completed:

- All 89 standalone tests pass on Python 3.11–3.14, including installed-wheel,
  import-boundary, and example lifecycle tests.
- All 260 HA regression tests pass against version 0.1.0 installed from PyPI.
- Formatting, lint, strict type checks, documentation checks, archive inspection,
  and clean cloud-only imports pass.

The HA migration ships in version 0.5.2 and was built and validated on the local
Home Assistant 2026.10.1 installation. Both Baseboard Lites reconnected locally,
cloud commands received device-report confirmation, and saved configuration and
entity IDs were preserved. See the [integration release notes](CHANGELOG.md#052-2026-10-12) and the
[client release instructions](https://github.com/SokolskyNikita/mysa-client#development-and-release).

The sections below retain the original migration plan and ownership decisions.

## Pre-extraction state

- `custom_components/mysa_local/protocols/cloud/` contains cloud authentication,
  HTTP requests, command encoding, response models, and errors. Its external
  runtime dependency is `aiohttp`.
- `custom_components/mysa_local/protocols/homekit/` contains Mysa's semantic
  HomeKit API, including discovery, pairing, characteristic mapping, device
  operations, and events. It uses `aiohomekit` for the underlying HAP transport.
- Neither protocol package imports Home Assistant or the other protocol.
  `tests/test_protocol_boundaries.py` enforces these boundaries and checks
  isolated imports.
- Normal imports through `custom_components.mysa_local` still initialize the
  integration's HA-dependent parent package. Moving the protocols into their own
  distribution removes that obstacle.
- Protocol tests are not yet independent: cloud tests use HA's HTTP session and
  test helpers, and shared fixtures live alongside integration fixtures.

## Proposed package

Use a separate repository and release cycle. The provisional distribution name
is `mysa-client`, with import package `mysa_client`; check name availability
before committing to either name.

```text
mysa-client/
  pyproject.toml
  README.md
  LICENSE
  src/mysa_client/
    __init__.py
    py.typed
    cloud/
    homekit/
  tests/
    cloud/
    homekit/
  examples/
```

Declare `aiohttp` as a runtime dependency and expose HomeKit support through an
optional extra, provisionally `mysa-client[homekit]`. Keep the root package from
eagerly importing HomeKit so cloud-only installations work without `aiohomekit`.
Keep the existing MIT license and attribution.

Choose the minimum Python version by checking the extracted code and its
dependencies, then testing that version in CI. Do not automatically inherit the
HA project's current `>=3.14.2` requirement.

## Ownership boundary

| Move into the library | Keep in the HA integration |
| --- | --- |
| Cloud authentication, token refresh, HTTP transport, endpoint selection, models, commands, and errors | Cloud polling, HA coordinator, state freshness and confirmation policy |
| HomeKit discovery, pairing exchanges, device operations, characteristic mapping, and events | HA discovery adaptation, pairing credential storage, and recovery UI |
| Protocol parsing and validation | Entities, configuration flows, services, and repairs |
| Protocol unit tests, sanitized fixtures, and protocol documentation | Integration tests, local/cloud routing, reconciliation, and HA lifecycle |

In particular, keep `cloud_coordinator.py`, `cloud_state.py`,
`homekit_adapter.py`, `local_adapter.py`, `pairing_store.py`, `thermostat.py`,
and `command_router.py` in this repository for the initial extraction.

The caller continues to own HTTP sessions, Zeroconf lifecycle, credential
persistence, and application polling. Preserve the callbacks for saving refresh
tokens and pairing credentials, including pairing persistence and rollback
behavior.

## Migration sequence

### 1. Establish the standalone package

- Copy the two protocol directories into the new package without changing
  protocol behavior or public method signatures.
- Preserve the existing cloud and HomeKit public exports as the starting API.
  Inventory direct submodule imports in the integration before defining which
  additional helpers must be supported.
- Add build metadata, explicit runtime dependencies, the HomeKit extra, the MIT
  license, and the `py.typed` marker.
- Verify that cloud imports work with only base dependencies and that neither
  protocol imports Home Assistant.

The temporary copy is for migration only. Once HA switches to the library,
remove the bundled implementation so protocol fixes have one source of truth.

### 2. Move and decouple protocol tests

- Move the relevant tests from `tests/api/`, `test_homekit_protocol.py`, and
  `test_pairing_protocol_failures.py` into the library.
- Review other test files for protocol-only cases; retain tests exercising HA
  lifecycle, routing, and entities in the integration.
- Replace HA session fixtures with ordinary `aiohttp.ClientSession` fixtures and
  replace HA HTTP mock helpers with standalone equivalents.
- Extract protocol fixtures and fake devices from `tests/common.py` without
  carrying over HA imports or the integration's autouse fixtures.
- Preserve coverage for authentication, refresh and retry behavior, response
  parsing, command encoding, pairing persistence, cancellation, and rollback.
- Adapt the import-boundary checks to the new package. Retain an integration
  check that direct `aiohomekit` usage remains confined to the library.

### 3. Document the public API

- Provide runnable examples for cloud login, device discovery, reading state,
  setting a temperature, saving refreshed credentials, and session cleanup.
- Provide HomeKit examples for discovery, pairing, saved-pairing reuse, events,
  reads, writes, and shutdown, including creation and cleanup of Zeroconf.
- Document session ownership, exceptions, timeout/retry behavior, and caller
  responsibilities for polling and persistence.
- Explain that command acceptance does not establish fresh or applied device
  state; confirmation policy remains the application's responsibility.
- Move relevant protocol references and sanitized examples, repair their links,
  and keep HA setup and workflow documentation in the integration repository.
- Clearly describe reverse-engineered API status, supported operations, and
  which hardware has actually been validated. Keep private research and account
  or pairing credentials out of the distribution and examples.

### 4. Validate and release the library

- Run standalone tests, lint, and type checks in CI across the declared Python
  versions.
- Build a wheel and source distribution and inspect their contents.
- Install the built wheel into a clean environment without HA and verify cloud
  imports with base dependencies and HomeKit imports with the optional extra.
- Validate the standalone examples; keep real-device checks separate from
  offline CI and avoid device-changing operations in automated smoke tests.
- Publish an initial version only after package validation. Use independent
  library versioning and document public API changes in release notes.

### 5. Switch the HA integration

- First test against a local development install of the extracted library.
- Replace protocol imports and affected test mock paths with `mysa_client`
  imports; update tools and documentation that use the old paths.
- After the library release is available, pin the tested package version in
  `custom_components/mysa_local/manifest.json`. For example, if the provisional
  name and version are used: `mysa-client[homekit]==0.1.0`.
- Update development dependencies and manifest logger declarations as needed.
  Let the library declare its own protocol dependencies.
- Remove the bundled protocol implementation and tests now owned by the
  library. Retain all integration-level behavior tests.
- Run the complete HA test suite and repository lint, type, and documentation
  checks against the packaged dependency before releasing the integration.

Release order is library first, then the integration version that depends on
it. Future protocol changes should follow the same sequence: library change and
release, followed by an integration dependency update and regression checks.

## Completion criteria

- A small standalone Python script can read a thermostat without HA installed.
- Cloud-only users can install and import the client without HomeKit dependencies.
- HomeKit users can discover, pair, reconnect using persisted credentials, and
  read devices through the library's semantic API.
- The installed wheel contains the required runtime modules and typing marker.
- Protocol tests run without HA or its testing plugin.
- HA passes its full regression suite against the released library, with
  existing configuration and persisted credentials still usable.
- Protocol code has one maintained implementation, and public documentation
  explains installation, lifecycle, support limits, and command semantics.

## Deferred work

Consider a transport-neutral device model or a unified local/cloud client only
after the extraction is stable and external consumers need it. That work would
require a separate design for routing, command ordering, freshness,
reconciliation, and persistence boundaries currently handled by HA code.

## References

- [Current integration architecture](docs/architecture.md)
- [Python packaging guide](https://packaging.python.org/en/latest/tutorials/packaging-projects/)
- [HA manifest requirements](https://developers.home-assistant.io/docs/creating_integration_manifest/#requirements)
