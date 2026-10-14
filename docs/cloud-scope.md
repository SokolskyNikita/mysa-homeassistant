# Home Assistant feature scope

<a id="cloud-feature-scope-for-home-assistant"></a>

This is the authoritative register of Home Assistant product decisions, independent of API flavor. See the [user guides](README.md) for operation and the [management inventory](implementation/progress.md) for implementation status and remaining validation gaps.
**Include** means useful to this integration; **Consider** means optional or
model-specific; **Skip** means no dedicated HA interface or routine polling.
They do not define the standalone library's scope or claim a feature is already
implemented. See the [management inventory](implementation/progress.md) for current
coverage and the [client API inventory](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/api-coverage.md)
for callable methods. Read/write labels describe the operation, not a host.

| Operation | What it does | HA recommendation |
| --- | --- | --- |
| <a id="discovery"></a>Account/device discovery · read | Finds homes, device IDs, models, names, and membership | **Include** the discovery needed for setup; do not build account administration |
| Capabilities · read | Describes supported modes, controls, ranges, and model features | **Include**; combine with observed valid readings, rather than trusting every flag |
| Room temperature / humidity · read | Reports ambient conditions | **Include**; climate and humidity entities |
| Setpoint / operating mode · read/write | Selects the requested temperature and heating/cooling/off mode | **Include**; core climate controls |
| AC fan / swing · read/write | Selects fan speed and vent direction | **Include when supported**; honor the AC remote's available commands |
| Heating activity / runtime · read | Reports recent duty and accumulated heating time | **Include**; distinguish interval activity from an instantaneous relay state |
| <a id="electrical-data"></a>Measured electrical data · read | Reports power/current/energy on hardware that measures it | **Consider by model**; skip Lite's zero/unmeasured fields and voltage writes for an energy dashboard; label calculated energy as an estimate |
| Setpoint limits · read/write | Restricts temperatures accepted at the thermostat | **Include**; hardware constraints remain useful with HA automations |
| Temperature offset · read/write | Calibrates the thermostat's ambient reading/control | **Include**; do not substitute an HA display-only correction |
| Button lock · read/write | Locks the physical controls | **Include** as device configuration |
| Wake / idle brightness · read/write | Sets the physical display's two brightness levels | **Include** as device configuration |
| Display units · read/write | Chooses Celsius/Fahrenheit on the thermostat screen | **Include**; independent of HA's display units |
| Heater profile · read/write | Selects the installed heater's switching profile | **Include** as installation configuration, not a routine automation target |
| Floor limits/probe, adaptive display, remote tracking, HVAC configuration · read/write | Configures model-specific sensors and control behavior | **Consider only on matching hardware**; Lite cannot validate these controls |
| Identity / installed firmware · read | Identifies hardware and current firmware | **Include** as device metadata |
| <a id="names-and-time-zones"></a>Device name / time zone · read/write | Stores Mysa's label and schedule time zone | **Include explicit name synchronization** in thermostat settings; read the time zone for schedules, and consider time-zone writes only for explicit Mysa schedule setup |
| Firmware availability / update · read/write | Checks for an offered version and requests installation | **Include**; user-initiated update, not an automatic consequence of polling |
| Cloud connection / data age · read | Describes API reachability and thermostat reporting freshness | **Include** separate health/freshness diagnostics |
| RSSI / internal temperatures / free heap · read | Helps diagnose radio, sensor, or firmware behavior | **Consider** diagnostics, disabled by default; not extra climate readings |
| Live reporting · write | Temporarily asks devices to upload more frequently | **Include** outage-driven HTTPS live reporting and renewable MQTT streams behind user options; see [HTTPS behavior](guides/behavior.md) and [MQTT policy](mqtt.md) |
| Usage / reading history · read | Supplies runtime totals and past measurements | **Include runtime**; **consider** history for gaps/troubleshooting, not a duplicate HA recorder |
| State-change history · read | Explains what changed a target or mode | **Consider** on demand when Mysa and HA behavior disagree |
| <a id="schedules"></a>Device schedules · read/write | Stores timed thermostat actions | **Include** inspection, replacement, clearing, and recovery through [schedule actions](guides/schedules.md); **exclude** an in-place or separate legacy schedule editor. Prefer HA scheduling for HA-managed automations |
| Hold / resume · read/write | Overrides a Mysa schedule temporarily or permanently, then resumes it | **Include when a Mysa schedule exists**; HA-only scheduling does not need a cloud hold for every command |
| <a id="shortcuts"></a>Home/Away/Vacation shortcuts · read/write | Applies a stored bundle of device actions | **Consider inspection/cleanup** for coexistence; **skip new shortcut creation/apply UI**, use HA scenes |
| <a id="demand-response"></a>Demand-response state · read | Indicates a utility event that may affect behavior | **Consider** a read-only explanation/status; skip enrollment management |
| Device claim / replacement / removal · write | Changes the device's association with a Mysa account | **Consider** explicit setup/recovery tools only; not normal thermostat control |
| <a id="templates"></a>Saved schedule templates · read/write | Stores reusable schedules and applies copies to devices | **Skip** template management; recognize applied device schedules through the schedule operation above |
| <a id="zones-and-energy"></a>Mysa zones / aggregate energy · read/write | Groups devices and summarizes a Mysa home/zone | **Skip**; use HA areas/groups and validated energy sources. Lite legacy summaries are unreliable; the separate whole-home energy API was unavailable on the tested account |
| <a id="geofences"></a>Geofences / crossings · read/write | Runs Mysa actions when a phone enters or leaves | **Skip**; use HA presence and automations |
| <a id="notifications"></a>Alerts / reminders / notifications · read/write | Stores thresholds, reminders, push tokens, and notification preferences; sends notifications | **Skip**; use HA automations and notification services |
| <a id="account-administration"></a>Mysa account/home/access management · read/write | Edits profiles/homes, transfers access, manages invitations, or deletes accounts | **Skip** beyond discovery required for setup |
| <a id="app-services"></a>App/third-party services · read/write | Handles app flags, phone diagnostics, analytics, localization, Alexa authorization, and property-manager features | **Skip**; they do not improve thermostat control in HA |
| <a id="obsolete-provisioning"></a>Obsolete provisioning helpers · read/write | Supplies legacy NFC or anonymous AC-setup credentials | **Skip**; separate from an authenticated HA thermostat client |
| <a id="app-settings"></a>Mysa eco / Early On / animation settings · read/write | Supplies app-specific energy/schedule behavior and display animation | **Skip a dedicated HA interface**; prioritize HA scheduling and the explicit hardware controls above. Early On is unavailable on Lite; eco/animation effects were not established |

## Transport boundaries

<a id="cloud-transports"></a>
Cloud host aliases are not independent measurements. Use selected acquisition
routes, per-field reconciliation, and one chosen write route; do not poll every
host continuously or broadcast writes. Discovery needs bootstrap, device
records, capabilities, and firmware metadata, without duplicate discovery UIs.

## HomeKit exclusions and deferrals

These decisions concern HA exposure. Protocol layouts and observations remain
in the client references linked below; unknown fields are not named features.

| Decision | Surface | HA decision and evidence |
| --- | --- | --- |
| <a id="hk-d01"></a>**HK-D01** · Deferred | Wi-Fi Transport / Configuration Control (`0x22A` / `0x22D`) | No semantic configuration API or HA Wi-Fi migration flow. A staged operation-3 write was accepted, but a successful network move was not established; operation 2 and completed operation 4 remain unverified. See the [client protocol findings](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/reference/protocol/homekit.md#homekit-wi-fi-switching) and [API-LOCAL-13](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/api-coverage.md#api-local-13). |
| <a id="hk-d02"></a>**HK-D02** · Deferred | Hidden vendor boolean | Purpose and physical effect remain unknown; do not expose as a named HA feature. Evidence and the sanitized accessory profile are in the [client accessory reference](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/reference/protocol/homekit.md#characteristic-coverage) and [fixture](https://github.com/SokolskyNikita/mysa-client/blob/main/tests/fixtures/homekit_bb_v2_0_l.json). |
| <a id="hk-o01"></a>**HK-O01** · Out of scope | Accessory Runtime Information (`0x239`) and extra information fields | Not thermostat controls or HA entities without a useful consumer; see [API-LOCAL-07](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/api-coverage.md#api-local-07) and the [client accessory profile](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/reference/protocol/homekit.md#characteristic-coverage). |
| <a id="hk-o02"></a>**HK-O02** · Out of scope on observed Lite | Humidity, button lock, brightness, schedules, and other cloud functions | These were absent from the observed Lite HomeKit profile. Do not infer local equivalents for cloud fields; revisit only with a different verified accessory. See [HomeKit findings](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/reference/protocol/homekit.md#characteristic-coverage). |
| <a id="hk-o03"></a>**HK-O03** · Out of scope | Firmware installation through HomeKit | No established HomeKit update operation; HomeKit reads firmware metadata only. Firmware update methods and evidence are tracked in the [client API map](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/api-coverage.md#api-read-19) and [firmware reference](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/reference/protocol/firmware-and-connectivity.md). |
| <a id="hk-o04"></a>**HK-O04** · Out of scope | BLE provisioning, Matter, and a new HAP transport | They are separate setup/protocol scopes. This adapter uses HAP over IP through `aiohomekit`; see the client [BLE](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/reference/protocol/bluetooth.md), [Matter](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/reference/protocol/matter.md), and [HomeKit package](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/reference/python/homekit.md) references. |
