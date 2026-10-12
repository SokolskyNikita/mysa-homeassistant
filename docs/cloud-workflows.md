# Cloud workflows in Mysa Local

<a id="cloud-account-and-schedule-workflows"></a>

The API routes, request bodies, observed responses, and app-derived account
behavior now live in the [`mysa-client` cloud workflow reference](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/cloud-workflows.md).
That page is the canonical source for the protocol details linked below. This
page records how those workflows relate to the Home Assistant integration and
keeps the previous section anchors for existing links.

- [Schedules, holds and shortcuts](#schedules-holds-and-shortcuts)
- [Account enrollment and removal](#adding-and-removing-devices)
- [Other account and app routes](#additional-endpoints)

## Schedules, holds and shortcuts

Mysa account schedules may still act on a thermostat alongside Home Assistant
commands. See the client reference for the observed request formats and
[schedule routes](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/cloud-workflows.md#schedules-holds-and-shortcuts).
Mysa Local replaces schedules by detaching and recreating them, so that update
is non-atomic; its [schedule code](../custom_components/mysa_local/schedule.py)
backs up the original and reconciles ambiguous failures before attempting
restoration.

### Weekly schedules

Wire fields, weekday encoding, device time-zone behavior, and observed update
responses are in the [client's weekly schedule notes](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/cloud-workflows.md#weekly-schedules).
Mysa Local currently uses schedule inspection and replacement to support its
existing schedule controls.

### Legacy schedules and saved templates

Legacy v1/v2 schedule fields, saved-template routes, and host differences are
documented in the [client reference](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/cloud-workflows.md#legacy-schedules-and-saved-templates).
Mysa Local does not provide a separate editor for saved templates.

### Holds

The [client hold notes](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/cloud-workflows.md#holds)
describe request fields and observed responses. Mysa Local uses a hold only
when coordinating with an existing Mysa schedule; ordinary local-first climate
commands do not require one.

### Home, Away and Vacation shortcuts

The [client shortcut reference](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/cloud-workflows.md#home-away-and-vacation-shortcuts)
covers read, edit, and apply routes. Mysa Local uses shortcut cleanup during
device removal; applying a shortcut remains an app workflow.

## Adding and removing devices

The client reference contains the account-claim and deletion request details,
including tested limitations and the [newer-model enrollment sequence](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/cloud-workflows.md#baseboard-v3-central-hvac-and-climate-sensor).
Wi-Fi onboarding and local pairing are separate integration flows; see the
client's [Bluetooth protocol reference](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/bluetooth.md#scope-and-compatibility).

### Claiming an older model

The [client's older-model claim notes](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/cloud-workflows.md#claiming-an-older-model)
document account fields, defaults, and 409 behavior. For a returning device,
Mysa Local requests the minimal account claim so existing settings are not
overwritten by new-device heater defaults.

### Deletion and re-addition

The account deletion effects and re-add observations are in the
[client reference](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/cloud-workflows.md#deletion-and-re-addition).
Mysa Local first attempts schedule and shortcut cleanup, then continues with
account deletion if cleanup fails. Sensor/HVAC-link and group cleanup are not
implemented.

### Baseboard V3, Central HVAC and Climate Sensor

The [client enrollment notes](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/cloud-workflows.md#baseboard-v3-central-hvac-and-climate-sensor)
record the app-derived routes. Integration entities, command behavior,
limitations, and hardware test steps remain in the
[newer-model integration guide](new-cloud-models.md).

## Firmware

Firmware installation is opt-in. When enabled, the integration calls the client
once to trigger an update, then polls every 20 seconds for up to six minutes.
Version 0.5.2 corrected the required empty JSON request body. An accepted trigger
was observed on an already up-to-date Lite; installing an offered release remains
unverified. The [firmware validation tracker](implementation/cloud.md#c-a01)
owns HA completion criteria, and the client's [firmware reference](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/firmware-and-connectivity.md#firmware)
owns endpoints and device observations.

## Additional endpoints

Observed availability, account/app routes, and example request mechanics are
listed in the client's [additional endpoint survey](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/cloud-workflows.md#additional-endpoints).
The Home Assistant integration tracker remains the source for current feature
scope and planned work: [cloud implementation status](implementation/cloud.md).

## Retained app-only workflow mechanics

The recovered app-only route survey and its evidence are maintained in the
client's [app workflow reference](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/cloud-workflows.md#retained-app-only-workflow-mechanics).
The presence of an app route does not imply that Mysa Local exposes or calls it.
