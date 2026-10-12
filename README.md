# Mysa Local for Home Assistant

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://www.hacs.xyz/docs/faq/custom_repositories/)
[![Validate](https://github.com/SokolskyNikita/mysa-homeassistant/actions/workflows/validate.yml/badge.svg)](https://github.com/SokolskyNikita/mysa-homeassistant/actions/workflows/validate.yml)
[![Tests](https://github.com/SokolskyNikita/mysa-homeassistant/actions/workflows/tests.yml/badge.svg)](https://github.com/SokolskyNikita/mysa-homeassistant/actions/workflows/tests.yml)

Control Mysa thermostats over your local network through HomeKit. Setpoint, mode, room temperature and heating state work without a Mysa account or internet connection. Setup uses Home Assistant's UI.

An optional Mysa sign-in adds settings, humidity, schedules, firmware checks and cloud control when the local connection fails.

## Supported models

Only the Baseboard Lite has been tested on hardware. Other models are supported in code with the limits below; their controls still need hardware testing.

| Model | Model IDs | Status |
|---|---|---|
| Baseboard Lite | `BB-V2-0-L` | Hardware-tested on firmware 3.17.5.13; cloud setpoint/mode application differed between the two tested units |
| Other Baseboard V1 / V2 models | `BB-V1-*`, `BB-V2-*` | Supported in code; untested |
| In-Floor | `INF-V1-0` | Supported in code; untested |
| Mini-Split AC | `AC-V1-0` | Supported in code; untested |
| Central HVAC | `ST-V1-0`, `ST-V1`, `LV` | Experimental cloud support after setup in the Mysa app |
| Baseboard V3 | `BB-V3-0` | Experimental cloud support after setup in the Mysa app |
| Climate Sensor | `CS-V1-0`, `CS-V1` | Experimental cloud readings; no climate entity |

For the experimental models, finish setup in the Mysa app, then sign in here and import them from your account. V3 provides basic heat/off and setpoint control. Central HVAC adds heat/cool auto targets and supported fan controls. Climate Sensor provides readings only. Controls depend on the returned capabilities. Schedules/holds, energy/runtime, sensor linking and newer-model OTA are deferred. See [experimental cloud support and the V3 test guide](docs/new-cloud-models.md).

AC fan and swing controls were implemented from Mysa app 4.20.1 and tested with simulated responses. They show the remote's options for the current mode and are hidden while the AC is off. Toggle-only swing, AC auto mode and AC heating/cooling target ranges are not implemented. In-Floor's floor-sensor reading is not exposed.

If you test another model, [submit a model report](https://github.com/SokolskyNikita/mysa-homeassistant/issues/new?template=model_report.yml) with its firmware version and integration diagnostics.

## Installation

Requires Home Assistant 2026.9 or newer.

With HACS:

1. Open HACS → menu → Custom repositories.
2. Add `https://github.com/SokolskyNikita/mysa-homeassistant` with type Integration.
3. Download Mysa Local and restart Home Assistant.

For a manual installation, copy [custom_components/mysa_local](custom_components/mysa_local) into your Home Assistant `config/custom_components/` directory, then restart.

## Setup

### Pair over your local network

1. Connect the thermostat to 2.4 GHz Wi-Fi. You can use the Mysa app or the separate [Mysa Wi-Fi setup page](https://sokolskynikita.github.io/mysa-wifi-setup/). The page uses Bluetooth in Chrome or Edge without the Mysa app or an account. A new or reset thermostat shows a pairing symbol, usually a Bluetooth icon.
2. Press a button on the thermostat to wake its HomeKit service. In Settings → Devices & services, add the discovered Mysa. You can also choose Add integration → Mysa Local → Pair a thermostat on this network.
3. Enter the eight-digit HomeKit code printed on the back of the faceplate. Spaces and dashes are accepted.
4. Sign in to Mysa for cloud features, or choose Skip, local only.

Home Assistant must be able to reach the thermostat and discover it through mDNS. If it is paired to Apple Home or another controller, remove that pairing before starting a new one. For an existing Home Assistant HomeKit Device entry, use the migration below.

To add another thermostat, use its discovery card or Add thermostat on the Mysa Local integration page.

Pairing credentials are saved as soon as the thermostat accepts them. If setup is interrupted, start it again and choose the saved pairing to continue without another HomeKit code.

### Move from HomeKit Device

Choose Move a thermostat from the HomeKit Device integration during setup or when adding a thermostat. The existing pairing must be reachable. Mysa Local uses it to add its own pairing, so the setup code is not required.

The migration transfers matching climate, temperature, display-units and Identify entities with their IDs and registry settings, unless an equivalent Mysa Local entity already exists. It also copies the device's name, area and labels, then removes the old HomeKit Device entry.

### Add thermostats from your Mysa account

Sign in during setup or through the integration's options, then select thermostats from the account. For models with HomeKit, you can pair each one locally with its code or leave it under cloud control. V3, Central HVAC and Climate Sensor use cloud control here. A thermostat without local pairing needs Mysa's servers and an internet connection to work.

Local pairing and Mysa account membership are separate. While signed in, Mysa Local offers to add a locally paired thermostat to the account if it is missing there. If the account has no home, create one in the Mysa app first.

Repairs notices identify thermostats that are only on the cloud, missing from the account or removed from it after setup. You can use the offered fix or ignore a notice when that arrangement is intentional.

## Entities and controls

Entities depend on the thermostat's capabilities and reported data. The [newer-model subset](docs/new-cloud-models.md#implemented-controls) excludes the deferred extras listed there. Local controls use cloud fallback where listed, provided the thermostat is on your account and cloud access is enabled.

| Entity or control | Connection | Behavior |
|---|---|---|
| Climate and room temperature | Local, with cloud fallback | Setpoint, supported modes, room temperature and reported heating state; also creates a separate temperature sensor |
| AC fan speed and swing | Cloud | Fan speed plus vertical and horizontal swing, where the remote supports them |
| Humidity | Cloud | Also shown on the climate entity; the Lite reports it despite its capability flag |
| Display units | Local, with cloud fallback | Celsius or Fahrenheit on the thermostat's screen; does not change Home Assistant's units |
| Identify | Local | Signals the thermostat so you can identify it |
| Button lock | Cloud | Locks or unlocks the thermostat's buttons |
| Wake and sleep brightness | Cloud | Five-percent steps; wake is at least 10% and sleep cannot exceed wake |
| Lowest and highest setpoint | Cloud | Hardware limits, also used by the climate entity |
| Temperature offset | Cloud | Adjusts the temperature used for control and the reported room temperature |
| Heater type | Cloud | Baseboard or fan forced on models that support those profiles |
| Heating runtime today | Observed heating state, optionally supplemented by cloud history | Resets at midnight; see [Runtime and energy](#runtime-and-energy) |
| Heater power and energy | Calculated | Created when you set a heater wattage |
| Firmware update | Cloud | Checks for updates; installation is optional and has a [known request gap](docs/implementation/cloud.md#c-a01) |
| Connection path | Integration status | `local`, `cloud` or `offline` |

Other status entities show HomeKit pairing, Mysa account membership, cloud connectivity and schedule hold. The Wi-Fi signal sensor is disabled by default; enable it from the device's entity list when needed.

## Options and account management

### Integration options

Open Mysa Local's options, then Cloud and polling.

| Option | Default | Effect |
|---|---|---|
| Use Mysa cloud | Enabled after sign-in | Allows cloud settings, data and fallback control |
| Cloud poll interval | 60 seconds | Reads state for all thermostats; accepts 30–300 seconds |
| Live mode during local outages | On | Requests faster readings while a locally paired thermostat's LAN connection is down |
| Advanced → Allow firmware installs | Off | Allows the Firmware update entity to start an offered update |

Turning cloud access off disables cloud-only entities while keeping their entity IDs, names, areas and history. Your sign-in is retained. Turning it back on restores entities disabled by the integration; entities you disabled yourself stay disabled. A thermostat configured only through the cloud is unavailable while cloud access is off.

Firmware installation has not been verified with an available update. A separate API test accepted the trigger when the Lite was already up to date; no firmware was installed. The integration currently omits the required request body, so its update command still needs correction. See the [known implementation gap](docs/implementation/cloud.md#c-a01) and [firmware reference](docs/firmware-and-connectivity.md#firmware).

The options menu also provides sign-in, sign-out and Thermostats on my Mysa account. The account table is read-only: it shows both names and whether each thermostat is paired locally, uses only the cloud or has not been added to Home Assistant.

### Saved sign-in

By default, Mysa Local stores your email and refresh token after sign-in. It renews short-lived API tokens automatically. If the saved sign-in expires or is revoked, Home Assistant asks you to sign in again; local control continues.

Select Save my password to let the integration sign in again automatically. This is optional and off by default. The password is stored unencrypted in Home Assistant's configuration. To change the choice, sign out and sign in again. Signing out deletes the token and any saved password.

### Per-thermostat settings

On the integration page, open the thermostat's menu → Settings and status. It shows local pairing and Mysa account membership. From there you can:

- Set heater wattage and choose whether cloud history contributes to heating runtime.
- Pair a thermostat locally with its HomeKit code.
- Add it to or remove it from your Mysa account.
- Copy its Mysa app name to Home Assistant or rename it in Mysa to match Home Assistant.

Remove from my Mysa account attempts to detach the thermostat's Mysa schedules and remove its shortcut actions before deleting the account record. Cleanup errors are logged and do not stop the removal. If paired locally, HomeKit control remains available. A thermostat without local pairing becomes unreachable.

Re-adding sends the name, home and time zone without the newer app's settings defaults. This avoids explicitly overwriting settings such as heater type. The live tests and limits of settings retention are described under [Adding and removing devices](docs/cloud-workflows.md#adding-and-removing-devices).

Deleting a thermostat from Home Assistant removes Mysa Local's HomeKit pairing. It does not remove the thermostat from your Mysa account.

## Schedules and holds

These actions require a Mysa sign-in, cloud access and a thermostat on that account. Creating or clearing a schedule also requires the thermostat to belong to a Mysa home. The actions target Mysa Local climate entities. Once a Mysa schedule is saved, Home Assistant does not need to stay running for it to run.

| Action | Effect |
|---|---|
| `mysa_local.hold` | Holds a temperature and optional mode over an existing Mysa schedule. Omit `until` for a permanent hold, or supply a future date and time. The state is applied immediately. |
| `mysa_local.resume_schedule` | Ends the hold and resumes the Mysa schedule |
| `mysa_local.set_schedule` | Replaces the thermostat's weekly Mysa schedule |
| `mysa_local.clear_schedule` | Removes the thermostat's Mysa schedule |

Schedule replacement saves a private backup before changing the cloud schedule. If a request fails, the integration checks what Mysa actually applied and restores the original when necessary. Unfinished recovery survives an HA restart and retries after cloud updates. Recovery preserves shared schedules and avoids overwriting unrelated changes made in the Mysa app.

For a weekday schedule:

```yaml
action: mysa_local.set_schedule
target:
  entity_id: climate.living_room
data:
  events:
    - days: [mon, tue, wed, thu, fri]
      time: "06:30"
      temperature: 21
    - days: [mon, tue, wed, thu, fri]
      time: "22:00"
      temperature: 18
```

Days are `mon` through `sun`. Each event accepts `hvac_mode: heat`, `cool` or `off`; the default is `heat`. Temperatures in these custom actions are Celsius. Schedule times use the thermostat's time zone, so check that it matches Home Assistant's.

To hold 22 °C until you resume the schedule:

```yaml
action: mysa_local.hold
target:
  entity_id: climate.living_room
data:
  temperature: 22
  hvac_mode: heat
```

A hold needs an existing Mysa schedule. For schedules managed entirely by Home Assistant, use automations that call the climate entity's normal actions. Those use local control while the thermostat is paired and reachable.

## Runtime and energy

Heating runtime today counts the time HomeKit reports the heater running. The option "Use the thermostat's own heating runtime" is enabled by default. When cloud access is available, it supplements that count with Mysa's history, including periods when Home Assistant was down. Cloud runtime is fetched every 15 minutes. During cloud fallback, the total advances with these measured history updates; fractional duty-cycle readings do not extend the local relay count. Without cloud history, the count pauses until HomeKit recovers. The displayed total keeps the larger count and does not decrease within a day.

Set Heater wattage to the combined rated wattage of the heaters controlled by that thermostat. A positive value adds power and energy sensors; zero turns them off. With local readings, power is that wattage while heating and zero while idle. With cloud readings, power uses Mysa's reported duty cycle, so it can show an average between zero and the configured wattage. Energy is runtime multiplied by wattage and can be used in the Energy dashboard. These are estimates, not electrical measurements. Runtime and energy update once a minute and reset at local midnight.

## Local and cloud behavior

HomeKit supplies push updates and a health check runs every 30 seconds. While the local connection is healthy, the climate entity uses its readings. Cloud-only data, such as humidity and brightness settings, still comes from Mysa if enabled.

Setpoint, screen-unit and HomeKit-supported mode changes try HomeKit first. Modes without a HomeKit equivalent, including fan-only and dry, go directly through the cloud. A local write has a five-second timeout. After a timeout, the integration reads back the local value before retrying through the cloud. For setpoint and mode changes, it also checks cloud-reported state. A device rejection is reported directly.

Cloud state uses one batch request for all thermostats per poll. Settings records, capabilities and other data refresh less often. Outside live mode, a thermostat samples about every 30 seconds but uploads the samples to Mysa in batches, so the latest cloud reading is often 15–30 minutes old even while the thermostat is connected and accepts commands. Cloud climate readings, humidity and Wi-Fi signal require an explicit online status and a telemetry timestamp no more than 90 minutes old or one minute ahead of Home Assistant's clock. Settings controls remain available when the cloud responds and reports the thermostat online; they do not depend on the temperature-report timestamp. Local climate control is unaffected by stale cloud telemetry.

With Live mode during local outages enabled (the default), a lost LAN connection triggers a request for readings about every five seconds and changes cloud polling to ten seconds. Normal polling resumes when local control returns. This option applies to thermostats with a local pairing; thermostats added only from the account use the normal poll interval.

The Connection path sensor shows the active route. If a paired thermostat's LAN path stays down for 15 minutes, a repair notice appears and clears after recovery. Cloud errors and rate limits cause polling to back off; writes are spaced apart.

## Privacy

Mysa Local stores HomeKit pairing keys and the Mysa credentials described under [Saved sign-in](#saved-sign-in). Sign-in uses SRP, so the password itself is not sent to Mysa.

With cloud access disabled, the integration stops contacting Mysa's servers. The thermostat may still maintain its own cloud connection; this option does not change its network access.

Downloaded diagnostics redact stored credentials, pairing keys, addresses, serial numbers, device names and account identifiers. Command history uses anonymous thermostat labels and generated correlation IDs. Only known semantic fields enter history; arbitrary server fields, request/response bodies and exception text are omitted. Integration debug messages use the same sanitized history events. Home Assistant and third-party libraries can still log personal data, so review both downloads before sharing.

## Troubleshooting

| Problem | What to check |
|---|---|
| Thermostat is missing from discovery or cannot be paired | Press a thermostat button to wake HomeKit, check Wi-Fi and mDNS connectivity, then retry. Remove an existing Apple Home pairing first. |
| Connection path keeps changing to `cloud` | Check the thermostat's Wi-Fi signal and consider a DHCP reservation. The optional Wi-Fi signal entity can help. |
| Cloud settings are unavailable | Check that you are signed in, Use Mysa cloud is enabled and the thermostat is on the account. Local controls can continue without them. |
| Pairing fails after removing the thermostat from HA | If it was offline during removal, its old HomeKit pairing may still exist. Reset that pairing on the thermostat before pairing again. |
| A hold is refused | Create a Mysa schedule first. For a timed hold, use an end time in the future. |

### Capture a command problem

No shell access or manual API requests are needed:

1. Open **Settings → Devices & services → Mysa Local**, open the integration menu (**⋮**), and select **Enable debug logging**.
2. Record the thermostat model, firmware, Mysa Local and HA versions, original settings, and the value you intend to request.
3. Reproduce once with a small temporary change. Note the time and timezone, requested values, and what HA, the Mysa app and the physical thermostat actually show. Allow time for cloud reporting.
4. Restore the original settings and note whether restoration worked.
5. On the same integration page, open the affected account entry's **⋮ → Download diagnostics**. **Download before reloading the integration or restarting HA.**
6. Select **Disable debug logging** and save the offered debug log. Review both files for personal data, then attach the diagnostics JSON and debug log to a [bug report](https://github.com/SokolskyNikita/mysa-homeassistant/issues/new?template=bug.yml) or [model report](https://github.com/SokolskyNikita/mysa-homeassistant/issues/new?template=model_report.yml), with your notes.

Diagnostics include an in-memory history of at most **500 events**, retained for **24 hours**, with an **8 KiB limit per event**. Large events omit value details and are marked truncated. At most 100 retained events are recurring polling/failure observations; identical noisy events are suppressed for a minute. History is collected even without debug logging, has no disk persistence, and is cleared on unload, reload or restart. The export includes the retained time window and eviction, suppression and capture-failure counts. Download promptly after reproducing.

Command events distinguish submission, HTTP acceptance, uncertain/lost responses, failures, configuration storage, unchanged values, supersession, expiry and device-reported confirmation. **Accepted does not mean applied.** `confirmed` means a qualifying device report matched the command; it does not independently prove a physical change. Compare with the app and thermostat. Earlier commands remain in history when a new command replaces their active tracking. Source timestamps, requested/baseline values, request routes/status/duration and anonymous correlation make the sequence reviewable.

System information also has a Mysa Local section with connection counts, sign-in status and cloud health: **Settings → System → Repairs → ⋮ → System information**.

## Other integration options

| Option | Control path | Requirements |
|---|---|---|
| Mysa Local | HomeKit on the LAN, with optional HTTPS cloud features and fallback | HomeKit code for local pairing; Mysa account for cloud features |
| [HomeKit Device](https://www.home-assistant.io/integrations/homekit_controller/) | Local HomeKit; available controls depend on the services the thermostat exposes | Built into HA; HomeKit pairing |
| [Mysa_HA](https://github.com/kgelinas/Mysa_HA) | Mysa's cloud through MQTT and HTTP | Custom HA integration, Mysa credentials and internet access |
| [mysa2mqtt](https://github.com/bourquep/mysa2mqtt/tree/main/packages/mysa2mqtt) | Mysa's cloud through a bridge to your MQTT broker and HA | Node.js or Docker, an MQTT broker, Mysa credentials and internet access |

Check each project's model and feature list before switching. Mysa Local's hardware testing is limited to the Lite; other integrations may expose controls it does not, including In-Floor sensor data.

## Development

Use Python 3.14.2 or newer in a virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
scripts/setup
```

Run commands from the repository root:

```bash
scripts/test     # pytest with coverage; accepts extra pytest arguments
scripts/lint     # Ruff formatting and fixes, then strict mypy
scripts/develop  # development Home Assistant using config/
```

CI also runs HACS validation and hassfest. The cloud and HomeKit protocols are provided by the standalone [`mysa-client` package](https://github.com/SokolskyNikita/mysa-client); this integration owns lifecycle, polling, persistence, and command ordering. See [Architecture](docs/architecture.md) for the public interfaces, ownership rules, and boundary tests. Cloud endpoints are documented in [the documentation index](docs/README.md); the [Bluetooth protocol reference](docs/bluetooth.md) covers Wi-Fi provisioning, network scanning, and newer-model credential transfer. Release history is in [CHANGELOG.md](CHANGELOG.md).

## Credits and license

Mysa Local uses [aiohomekit](https://github.com/Jc2k/aiohomekit) for HomeKit. [Mysa_HA](https://github.com/kgelinas/Mysa_HA) and [mysotherm](https://github.com/dlenski/mysotherm) supplied earlier work on Mysa's cloud protocols.

Released under the [MIT License](LICENSE). This project is not affiliated with Mysa or Empowered Homes Inc.

Technical references and implementation status: [documentation index](docs/README.md).
