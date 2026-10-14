# Entities, options, and account management

[Documentation index](../README.md)

## Entities and controls

Entities depend on the thermostat's capabilities and reported data. The [newer-model subset](../new-cloud-models.md#implemented-controls) excludes the deferred extras listed there. Local controls use cloud fallback where listed, provided the thermostat is on your account and cloud access is enabled.

| Entity or control | Connection | Behavior |
|---|---|---|
| Climate and room temperature | Local, with cloud fallback | Setpoint, supported modes, room temperature and reported heating state; also creates a separate temperature sensor |
| AC fan speed and swing | Cloud | Fan speed plus vertical and horizontal swing, where the remote supports them |
| Humidity | MQTT, with cloud fallback | Also shown on the climate entity; see the [MQTT guide](../mqtt.md) |
| Display units | Local, with cloud fallback | Celsius or Fahrenheit on the thermostat's screen; does not change Home Assistant's units |
| Identify | Local | Signals the thermostat so you can identify it |
| Button lock | Cloud | Locks or unlocks the thermostat's buttons |
| Wake and sleep brightness | Cloud | Five-percent steps; wake is at least 10% and sleep cannot exceed wake |
| Lowest and highest setpoint | Cloud | Hardware limits, also used by the climate entity |
| Temperature offset | Cloud | Adjusts the temperature used for control and the reported room temperature |
| Heater type | Cloud | Baseboard or fan forced on models that support those profiles |
| Heating duty cycle | MQTT, with cloud fallback | Numeric percentage when reported; its `source` attribute identifies the current source |
| Heating runtime today | Observed heating state, optionally supplemented by cloud history | Resets at midnight; see [Runtime and energy](#runtime-and-energy) |
| Heater power and energy | Calculated | Created when you set a heater wattage; a fresh numeric duty cycle refines estimated power |
| Firmware update | Cloud | Checks for updates; installation is optional and has not been validated end to end |
| Connection path | Integration status | `local`, `cloud` or `offline` |

Other status entities show HomeKit pairing, Mysa account membership, cloud connectivity and schedule hold. The Wi-Fi signal sensor is disabled by default; enable it from the device's entity list when needed.

## Options and account management

### Integration options

Open Mysa Local's options, then Cloud and polling.

| Option | Default | Effect |
|---|---|---|
| Use Mysa cloud | Enabled after sign-in | Required for MQTT; allows cloud settings, data and fallback control |
| Cloud poll interval | 60 seconds | Reads state for all thermostats; accepts 30–300 seconds |
| Live mode during local outages | On | Requests faster readings while a locally paired thermostat's LAN connection is down |
| Live MQTT readings | On | Master switch for every thermostat; see the [MQTT guide](../mqtt.md) for the per-device option and fallback behavior |
| Advanced → Allow firmware installs | Off | Allows the Firmware update entity to start an offered update |

Turning cloud access off disables cloud-only entities while keeping their entity IDs, names, areas and history. Your sign-in is retained. Turning it back on restores entities disabled by the integration; entities you disabled yourself stay disabled. A thermostat configured only through the cloud is unavailable while cloud access is off.

Firmware installation has not been verified with an available update. See the [validation limits](../implementation/progress.md#mgt-quality-04) and [firmware reference](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/reference/protocol/firmware-and-connectivity.md#firmware).

The options menu also provides sign-in, sign-out and Thermostats on my Mysa account. The account table is read-only: it shows both names and whether each thermostat is paired locally, uses only the cloud or has not been added to Home Assistant.

### Saved sign-in

By default, Mysa Local stores your email and refresh token after sign-in. It renews short-lived API tokens automatically. If the saved sign-in expires or is revoked, Home Assistant asks you to sign in again; local control continues.

Select Save my password to let the integration sign in again automatically. This is optional and off by default. The password is stored unencrypted in Home Assistant's configuration. To change the choice, sign out and sign in again. Signing out deletes the token and any saved password.

### Per-thermostat settings

On the integration page, open the thermostat's menu → **Settings and status**. Choose **Settings** to change heater wattage, heating runtime, or MQTT for a standard thermostat; experimental cloud-only models have a separate **Live MQTT readings** menu item. This page also shows local pairing and Mysa account membership. From there you can:

- Set heater wattage and choose whether cloud history contributes to heating runtime.
- Turn **Use live MQTT readings** on or off for this thermostat. It is on by default; cloud access and the integration-level option must also be enabled. See the [MQTT guide](../mqtt.md) for the menu location on experimental cloud-only models.
- Pair a thermostat locally with its HomeKit code.
- Add it to or remove it from your Mysa account.
- Copy its Mysa app name to Home Assistant or rename it in Mysa to match Home Assistant.

Remove from my Mysa account attempts to detach the thermostat's Mysa schedules and remove its shortcut actions before deleting the account record. Cleanup errors are logged and do not stop the removal. If paired locally, HomeKit control remains available. A thermostat without local pairing becomes unreachable.

Re-adding sends the name, home and time zone without the newer app's settings defaults. This avoids explicitly overwriting settings such as heater type. The live tests and limits of settings retention are described under [Adding and removing devices](../cloud-workflows.md#adding-and-removing-devices).

Deleting a thermostat from Home Assistant removes Mysa Local's HomeKit pairing. It does not remove the thermostat from your Mysa account.

## Runtime and energy

Heating runtime today counts the time HomeKit reports the heater running. The option "Use the thermostat's own heating runtime" is enabled by default. When cloud access is available, it supplements that count with Mysa's history, including periods when Home Assistant was down. Cloud runtime is fetched every 15 minutes. During cloud fallback, the total advances with these measured history updates; fractional duty-cycle readings do not extend the local relay count. Without cloud history, the count pauses until HomeKit recovers. The displayed total keeps the larger count and does not decrease within a day.

Set Heater wattage to the combined rated wattage of the heaters controlled by that thermostat. A positive value adds power and energy sensors; zero turns them off. A fresh MQTT duty-cycle fraction refines estimated power; otherwise the existing local or cloud fallback applies. See the [MQTT guide](../mqtt.md) for the source order. Energy is runtime multiplied by wattage and can be used in the Energy dashboard. These are estimates, not electrical measurements. Runtime and energy update once a minute and reset at local midnight.

## Privacy

Mysa Local stores HomeKit pairing keys and the Mysa credentials described under [Saved sign-in](#saved-sign-in). Sign-in uses SRP, so the password itself is not sent to Mysa.

With cloud access disabled, the integration stops contacting Mysa's servers. The thermostat may still maintain its own cloud connection; this option does not change its network access.

Downloaded diagnostics redact stored credentials, pairing keys, addresses, serial numbers, device names and account identifiers. Command history uses anonymous thermostat labels and generated correlation IDs. Only known semantic fields enter history; arbitrary server fields, request/response bodies and exception text are omitted. Integration debug messages use the same sanitized history events. Home Assistant and third-party libraries can still log personal data, so review both downloads before sharing.
