# Debug and report a problem

[Documentation index](../README.md)

How to capture what Mysa Local did, and send a report someone can act on. It
needs no shell access and no manual API calls.

Use it when something doesn't work. Use it as well when you own a model that
hasn't been tested and everything works: a report from real hardware is the
only way a model moves from "untested" to "reported working" on the
[compatibility page](../compatibility.md), so logs are welcome either way.

## The short version

1. Turn on debug logging.
2. Make one small change and note what happened.
3. Download diagnostics before reloading or restarting anything.
4. Turn off debug logging and save the log.
5. Attach both files to a [bug report](https://github.com/SokolskyNikita/mysa-homeassistant/issues/new?template=bug.yml) or a [model report](https://github.com/SokolskyNikita/mysa-homeassistant/issues/new?template=model_report.yml).

## Capture a problem

1. Open **Settings → Devices & services → Mysa Local**, open the integration
   menu (**⋮**) and select **Enable debug logging**.
2. Write down the thermostat's model and firmware, the Mysa Local and Home
   Assistant versions, the current settings, and the value you're about to
   request.
3. Reproduce once, with a small temporary change. Note the time and time zone,
   the value you asked for, and what Home Assistant, the Mysa app and the
   thermostat's own screen each show. Give cloud changes a moment to report.
4. Put the original settings back, and note whether that worked.
5. On the same page, open the account entry's **⋮ → Download diagnostics**.
   **Do this before reloading the integration or restarting Home Assistant**:
   the history it contains is kept in memory only.
6. Select **Disable debug logging** and save the log it offers.
7. Read both files for personal data, then attach them to your report with
   your notes.

## Test a model nobody has reported on

Work through these and capture as above. Stop and report at the first step
that fails; a partial report is still useful.

1. **Set it up in the Mysa app first** and check that its basic controls work
   there.
2. **Add it to Mysa Local.** Models with local control are paired over
   HomeKit; see [setup](setup.md). The experimental models (Baseboard V3,
   Central HVAC, Climate Sensor) are imported from your Mysa account and must
   not ask for HomeKit pairing.
3. **Compare the readings** with the app: temperature, humidity, mode and
   target.
4. **Change the setpoint by a small amount** from Home Assistant. Check that
   the app and the thermostat follow.
5. **Change the mode** (off and heat, or the modes your model offers), then any
   display or lock controls it shows.
6. **Change something in the Mysa app** and check that Home Assistant picks it
   up.
7. **Restore the original settings,** then download diagnostics.

On the experimental models, don't test schedules, holds or other features the
[compatibility page](../compatibility.md#experimental-models) lists as not
available.

## What the files contain

- **Diagnostics** hold a history of at most 500 events from the last 24 hours,
  each limited to 8 KiB; larger events drop their value details and are marked
  truncated. At most 100 of them are recurring polling or failure observations,
  and identical noisy events are suppressed for a minute. The history is
  collected even without debug logging, is never written to disk, and is
  cleared on unload, reload or restart. The export states the time window and
  how many events were evicted, suppressed or failed to capture.
- **Identifiers are redacted,** but review the files yourself before sharing.
- **System information** has a Mysa Local section with connection counts,
  sign-in status and cloud health: **Settings → System → Repairs → ⋮ → System
  information**.

## Reading a command's history

Command events distinguish submission, HTTP acceptance, uncertain or lost
responses, failures, configuration storage, unchanged values, supersession,
expiry and device-reported confirmation.

- **Accepted does not mean applied.** Mysa's server taking a request says
  nothing about the thermostat.
- **`confirmed`** means a later report from the device matched the command. It
  still isn't proof of a physical change, so compare with the app and the
  thermostat.
- **Earlier commands stay in the history** when a newer one replaces them.
  Timestamps, requested and previous values, and the route, status and duration
  of each request make the sequence reviewable.
