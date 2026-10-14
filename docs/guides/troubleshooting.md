# Troubleshooting and diagnostic reports

[Documentation index](../README.md)

## Common symptoms

| Problem | What to check |
|---|---|
| Thermostat is missing from discovery or cannot be paired | Press a thermostat button to wake HomeKit, check Wi-Fi and mDNS connectivity, then retry. Remove an existing Apple Home pairing first. |
| Connection path keeps changing to `cloud` | Check the thermostat's Wi-Fi signal and consider a DHCP reservation. The optional Wi-Fi signal entity can help. |
| Cloud settings are unavailable | Check that you are signed in, Use Mysa cloud is enabled and the thermostat is on the account. Local controls can continue without them. |
| Pairing fails after removing the thermostat from HA | If it was offline during removal, its old HomeKit pairing may still exist. Reset that pairing on the thermostat before pairing again. |
| Temperature is unavailable although cloud settings work | Check the reading timestamp and HA clock. Settings can remain available with stale telemetry; see [reading freshness](behavior.md#reading-freshness). |
| Humidity or heating duty cycle switches away from MQTT | Check the global and device MQTT switches and cloud sign-in. MQTT readings expire independently; see [MQTT fallback](../mqtt.md#values-and-fallback). |
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
