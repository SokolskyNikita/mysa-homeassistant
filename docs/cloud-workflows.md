# Cloud workflows in Mysa Local

[Documentation index](README.md)

<a id="cloud-account-and-schedule-workflows"></a>

The API routes, request bodies, observed responses, and app-derived account
behavior now live in the [`mysa-client` cloud workflow reference](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/reference/protocol/cloud-workflows.md).
That page is the canonical source for the protocol details linked below. This
page records how those workflows relate to the Home Assistant integration and
keeps the previous section anchors for existing links.

For user actions and YAML examples, start with [schedules and holds](guides/schedules.md). For account controls, see [account management](guides/usage.md#per-thermostat-settings). This page explains the HA adapters.

- [Schedules, holds and shortcuts](#schedules-holds-and-shortcuts)
- [Account enrollment and removal](#adding-and-removing-devices)
- [Other account and app routes](#additional-endpoints)

## Schedules, holds and shortcuts

Mysa account schedules may still act on a thermostat alongside Home Assistant
commands. See the client reference for the observed request formats and
[schedule routes](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/reference/protocol/cloud-workflows.md#schedules-holds-and-shortcuts).
Mysa Local delegates schedule comparison, replacement, clear, and recovery to
the client's `ScheduleWorkflow`. HA's [schedule adapter](../custom_components/mysa_local/schedule.py)
provides durable storage, per-device serialization, startup/retry scheduling,
and translated service errors. Replacement still detaches and recreates
schedules, so the cloud operation is non-atomic; the client journal protects
the prior schedule through ambiguous failures.

### Weekly schedules

Wire fields, weekday encoding, device time-zone behavior, and observed update
responses are in the [client's weekly schedule notes](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/reference/protocol/cloud-workflows.md#weekly-schedules).
Mysa Local currently uses schedule inspection and replacement to support its
existing schedule controls.

### Legacy schedules and saved templates

Legacy v1/v2 schedule fields, saved-template routes, and host differences are
documented in the [client reference](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/reference/protocol/cloud-workflows.md#legacy-schedules-and-saved-templates).
Mysa Local does not provide a separate editor for saved templates.

### Holds

The [client hold notes](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/reference/protocol/cloud-workflows.md#holds)
describe request fields, observed responses, and reusable hold resolution and
application helpers. Mysa Local uses a hold only when coordinating with an
existing Mysa schedule; ordinary local-first climate commands do not require
one. HA supplies current state and transport callbacks and presents errors.

### Home, Away and Vacation shortcuts

The [client shortcut reference](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/reference/protocol/cloud-workflows.md#home-away-and-vacation-shortcuts)
covers read, edit, and apply routes. Mysa Local uses shortcut cleanup during
device removal; applying a shortcut remains an app workflow.

## Adding and removing devices

The client reference contains the account-claim and deletion request details,
including tested limitations and the [newer-model enrollment sequence](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/reference/protocol/cloud-workflows.md#baseboard-v3-central-hvac-and-climate-sensor).
Wi-Fi onboarding and local pairing are separate integration flows; see the
client's [Bluetooth protocol reference](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/reference/protocol/bluetooth.md#scope-and-compatibility).

### Claiming an older model

The [client's older-model claim notes](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/reference/protocol/cloud-workflows.md#claiming-an-older-model)
document account fields, defaults, and 409 behavior. The client account helper
builds the minimal claim for a returning device so app defaults do not
overwrite existing settings; HA gathers the user's home, name, and time-zone
choices and reports the result.

### Deletion and re-addition

The account deletion effects and re-add observations are in the
[client reference](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/reference/protocol/cloud-workflows.md#deletion-and-re-addition).
The client's account workflow attempts schedule and shortcut cleanup, returns
counts and a structured cleanup error, then continues with account deletion.
Mysa Local turns that result into its user-facing operation status.
Sensor/HVAC-link and group cleanup are not implemented.

### Baseboard V3, Central HVAC and Climate Sensor

The [client enrollment notes](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/reference/protocol/cloud-workflows.md#baseboard-v3-central-hvac-and-climate-sensor)
record the app-derived routes. Integration entities, command behavior,
limitations, and hardware test steps remain in the
[newer-model integration guide](new-cloud-models.md).

## Firmware

Firmware installation is opt-in. When enabled, the integration calls the client
once to trigger an update, then polls every 20 seconds for up to six minutes.
Version 0.5.2 corrected the required empty JSON request body. An accepted trigger
was observed on an already up-to-date Lite; installing an offered release remains
unverified. The [firmware validation tracker](implementation/progress.md#mgt-quality-04)
owns HA completion criteria, and the client's [firmware reference](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/reference/protocol/firmware-and-connectivity.md#firmware)
owns endpoints and device observations.

## Additional endpoints

Observed availability, account/app routes, and example request mechanics are
listed in the client's [additional endpoint survey](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/reference/protocol/cloud-workflows.md#additional-endpoints).
The [management inventory](implementation/progress.md) owns HA acceptance criteria and planned work. Product boundaries live in [feature scope](cloud-scope.md).

## Retained app-only workflow mechanics

The recovered app-only route survey and its evidence are maintained in the
client's [app workflow reference](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/reference/protocol/cloud-workflows.md#retained-app-only-workflow-mechanics).
The presence of an app route does not imply that Mysa Local exposes or calls it.
