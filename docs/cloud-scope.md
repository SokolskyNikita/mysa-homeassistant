# Cloud feature scope for Home Assistant

These are Home Assistant product recommendations, independent of API flavor.
**Include** means useful to this integration; **Consider** means optional or
model-specific; **Skip** means no dedicated HA interface or routine polling.
They do not define the standalone library's scope or claim a feature is already
implemented. See the [HA cloud tracker](implementation/cloud.md) for current
behavior and the [client API inventory](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/api-coverage.md)
for callable methods. Read/write labels describe the operation, not a host.

| Operation | What it does | HA recommendation |
| --- | --- | --- |
| Account/device discovery · read | Finds homes, device IDs, models, names, and membership | **Include** the discovery needed for setup; do not build account administration |
| Capabilities · read | Describes supported modes, controls, ranges, and model features | **Include**; combine with observed valid readings, rather than trusting every flag |
| Room temperature / humidity · read | Reports ambient conditions | **Include**; climate and humidity entities |
| Setpoint / operating mode · read/write | Selects the requested temperature and heating/cooling/off mode | **Include**; core climate controls |
| AC fan / swing · read/write | Selects fan speed and vent direction | **Include when supported**; honor the AC remote's available commands |
| Heating activity / runtime · read | Reports recent duty and accumulated heating time | **Include**; distinguish interval activity from an instantaneous relay state |
| Measured electrical data · read | Reports power/current/energy on hardware that measures it | **Consider by model**; skip Lite's zero/unmeasured fields; label calculated energy as an estimate |
| Setpoint limits · read/write | Restricts temperatures accepted at the thermostat | **Include**; hardware constraints remain useful with HA automations |
| Temperature offset · read/write | Calibrates the thermostat's ambient reading/control | **Include**; do not substitute an HA display-only correction |
| Button lock · read/write | Locks the physical controls | **Include** as device configuration |
| Wake / idle brightness · read/write | Sets the physical display's two brightness levels | **Include** as device configuration |
| Display units · read/write | Chooses Celsius/Fahrenheit on the thermostat screen | **Include**; independent of HA's display units |
| Heater profile · read/write | Selects the installed heater's switching profile | **Include** as installation configuration, not a routine automation target |
| Floor limits/probe, adaptive display, remote tracking, HVAC configuration · read/write | Configures model-specific sensors and control behavior | **Consider only on matching hardware**; Lite cannot validate these controls |
| Identity / installed firmware · read | Identifies hardware and current firmware | **Include** as device metadata |
| Device name / time zone · read/write | Stores Mysa's label and schedule time zone | **Read** for discovery/schedule interpretation; **skip cloud rename**, consider time-zone writes only for explicit Mysa schedule setup |
| Firmware availability / update · read/write | Checks for an offered version and requests installation | **Include**; user-initiated update, not an automatic consequence of polling |
| Cloud connection / data age · read | Describes API reachability and thermostat reporting freshness | **Include** separate health/freshness diagnostics |
| RSSI / internal temperatures / free heap · read | Helps diagnose radio, sensor, or firmware behavior | **Consider** diagnostics, disabled by default; not extra climate readings |
| Live reporting · write | Temporarily asks devices to upload more frequently | **Consider** for cloud responsiveness and troubleshooting; not a permanent background stream by default |
| Usage / reading history · read | Supplies runtime totals and past measurements | **Include runtime**; **consider** history for gaps/troubleshooting, not a duplicate HA recorder |
| State-change history · read | Explains what changed a target or mode | **Consider** on demand when Mysa and HA behavior disagree |
| Device schedules · read/write | Stores timed thermostat actions | **Include inspection/removal** of conflicting existing schedules; **exclude** an in-place schedule editor in HA. Prefer HA scheduling |
| Hold / resume · read/write | Overrides a Mysa schedule temporarily or permanently, then resumes it | **Include when a Mysa schedule exists**; HA-only scheduling does not need a cloud hold for every command |
| Home/Away/Vacation shortcuts · read/write | Applies a stored bundle of device actions | **Consider inspection/cleanup** for coexistence; **skip new shortcut creation/apply UI**, use HA scenes |
| Demand-response state · read | Indicates a utility event that may affect behavior | **Consider** a read-only explanation/status; skip enrollment management |
| Device claim / replacement / removal · write | Changes the device's association with a Mysa account | **Consider** explicit setup/recovery tools only; not normal thermostat control |
| Saved schedule templates · read/write | Stores reusable schedules and applies copies to devices | **Skip** template management; recognize applied device schedules through the schedule operation above |
| Mysa zones / aggregate energy · read/write | Groups devices and summarizes a Mysa home/zone | **Skip**; use HA areas/groups and validated energy sources |
| Geofences / crossings · read/write | Runs Mysa actions when a phone enters or leaves | **Skip**; use HA presence and automations |
| Alerts / reminders / notifications · read/write | Stores thresholds, reminders, push tokens, and notification preferences; sends notifications | **Skip**; use HA automations and notification services |
| Mysa account/home/access management · read/write | Edits profiles/homes, transfers access, manages invitations, or deletes accounts | **Skip** beyond discovery required for setup |
| App/third-party services · read/write | Handles app flags, phone diagnostics, analytics, localization, Alexa authorization, and property-manager features | **Skip**; they do not improve thermostat control in HA |
| Obsolete provisioning helpers · read/write | Supplies legacy NFC or anonymous AC-setup credentials | **Skip**; separate from an authenticated HA thermostat client |
| Mysa eco / Early On / animation settings · read/write | Supplies app-specific energy/schedule behavior and display animation | **Skip a dedicated HA interface**; prioritize HA scheduling and the explicit hardware controls above. Early On is unavailable on Lite; eco/animation effects were not established |

