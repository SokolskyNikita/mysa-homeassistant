# Troubleshooting and diagnostic reports

[Documentation index](../README.md)

## Common symptoms

| Problem | What to check |
|---|---|
| Thermostat is missing from discovery or cannot be paired | Press a thermostat button to wake HomeKit, check Wi-Fi and mDNS connectivity, then retry. Remove an existing Apple Home pairing first. Mysa Local refuses a thermostat that reports being paired to another controller; if it is no longer in Apple Home, reset it (hold the up and down buttons until the smile appears), connect it to Wi-Fi again in the Mysa app, then retry. |
| A Baseboard V1 reboots when you submit the setup code | Seen on firmware 3.17.5.11 ([#26](https://github.com/SokolskyNikita/mysa-homeassistant/issues/26)). Fully reset it: hold the up and down buttons for about 10 seconds until the smile appears, set it up again in the Mysa app, then pair. One reporter also had to make their Wi-Fi temporarily 2.4 GHz-only. |
| Connection path keeps changing to `cloud` | Check the thermostat's Wi-Fi signal and consider a DHCP reservation. The optional Wi-Fi signal entity can help. |
| Cloud settings are unavailable | Check that you are signed in, Use Mysa cloud is enabled and the thermostat is on the account. Local controls can continue without them. |
| Pairing fails after removing the thermostat from HA | If it was offline during removal, its old HomeKit pairing may still exist. Reset that pairing on the thermostat before pairing again. |
| Temperature is unavailable although cloud settings work | Check the reading timestamp and HA clock. Settings can remain available with stale telemetry; see [reading freshness](behavior.md#reading-freshness). |
| Humidity or heating duty cycle switches away from MQTT | Check the global and device MQTT switches and cloud sign-in. MQTT readings expire independently; see [MQTT fallback](../mqtt.md#values-and-fallback). |
| A hold is refused | Create a Mysa schedule first. For a timed hold, use an end time in the future. |

### Capture a command problem

Turn on debug logging, reproduce once, download diagnostics before any reload
or restart, and attach both files to a report. The full steps, what the files
contain and how to read a command's history are in
[Debug and report a problem](debugging.md#capture-a-problem).

## Pairing and discovery

If setup was interrupted after the thermostat accepted the code, retry setup and
choose the saved pairing. If the thermostat was reset or its HomeKit pairing was
removed elsewhere, the saved keys may no longer work. Check the thermostat's
pairing state and follow the explicit pairing/recovery flow; remembering a code
does not automatically re-pair it. See [setup and migration](setup.md) and
[remembering or forgetting setup codes](setup.md#remembering-setup-codes).

If an existing HomeKit Device integration is working, use
[Move from HomeKit Device](setup.md#move-from-homekit-device). An unreachable
pairing cannot be migrated. Avoid deleting a working entry just to retry setup.

## Commands and schedules disagree

Check whether a Mysa schedule or hold is active. Use the
[schedule actions](schedules.md) to resume, replace, or clear it as appropriate.
For an uncertain command result, compare HA, the app, and the thermostat before
repeating it, then [capture the command problem](#capture-a-command-problem).
An accepted cloud request does not establish that the thermostat applied it.
