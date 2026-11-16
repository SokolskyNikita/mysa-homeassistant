# Model compatibility

[Documentation index](README.md)

What is known to work on each Mysa model, and how it is known. The maintainer
owns two Baseboard Lites; everything else comes from community reports or from
reading Mysa's app, so check the status before relying on a control.

| Status | Meaning |
|---|---|
| Hardware-tested | Exercised by the maintainer on a real thermostat |
| Owner report | An owner reported what worked and what didn't in an issue; the outcome is stated per model |
| Untested | Implemented from Mysa's app and checked with simulated responses only |
| Experimental | A newer model, controlled through Mysa's cloud only: no local control, so it needs a Mysa account and internet access |

## Summary

| Model | Model IDs | Control path | Status | Firmware seen |
|---|---|---|---|---|
| [Baseboard Lite](#baseboard-lite) | `BB-V2-0-L` | Local HomeKit; optional cloud and MQTT | Hardware-tested: local and cloud control work | 3.17.5.13 |
| [Baseboard V2](#baseboard-v2) | `BB-V2-*` | Local HomeKit; optional cloud | One owner report: °F setpoints were rejected before 0.9.0; the fix is unconfirmed | Not reported |
| [Baseboard V1](#baseboard-v1) | `BB-V1-*` | Local HomeKit; optional cloud | Owner reports: discovered since 0.9.0, but some units reboot during pairing | 3.17.5.11 |
| [In-Floor](#in-floor) | `INF-V1-0`, `INF-V1` | Local HomeKit; optional cloud | One owner report: modes and setpoints work locally | 3.17.6.5 |
| [Mini-Split AC](#mini-split-ac) | `AC-V1-0`, `AC-V1` | Local HomeKit; optional cloud | Untested | None |
| [Central HVAC](#central-hvac) | `ST-V1-0`, `ST-V1`, `LV` | Mysa's cloud only | Experimental and untested | None |
| [Baseboard V3](#baseboard-v3) | `BB-V3-0` | Mysa's cloud only | Experimental. One owner report: import and readings work; commands from Home Assistant untested | 5.1.15 |
| [Climate Sensor](#climate-sensor) | `CS-V1-0`, `CS-V1` | Mysa's cloud only | Experimental and untested | None |

If you own a model that is untested or thinly reported, a report helps even
when everything works. [Debug and report a problem](guides/debugging.md) has
the steps; send the result as a [model report](https://github.com/SokolskyNikita/mysa-homeassistant/issues/new?template=model_report.yml).

## Baseboard Lite

Two units on firmware 3.17.5.13, observed 10–12 October 2026 and again for the
0.9 releases in November 2026.

- **Local control:** setpoint, mode, room temperature and heating state over
  HomeKit, without a Mysa account.
- **Cloud:** selected settings and the schedule workflows were exercised. Cloud
  setpoint and mode changes were applied by one unit and not by the other; see
  [reading freshness](guides/behavior.md) for how the integration confirms a
  change.
- **MQTT:** humidity and heating duty-cycle readings were validated on both,
  including the per-thermostat switch. Long-term MQTT availability is
  unverified, so readings expire after 30 seconds and fall back to cloud data;
  see the [MQTT guide](mqtt.md).
- **Fahrenheit:** the whole-°F card, limits and range were checked on a
  Fahrenheit unit system.
- **Not tested:** installing an offered firmware release.

## Baseboard V2

Supported with the same code as the Lite. One owner confirmed that °F setpoints
were rejected before 0.9.0 rounded them to the thermostat's step
([#32](https://github.com/SokolskyNikita/mysa-homeassistant/issues/32)). No
other controls have been reported.

## Baseboard V1

Reports cover `BB-V1-0` and `BB-V1-1` on firmware 3.17.5.11
([#26](https://github.com/SokolskyNikita/mysa-homeassistant/issues/26)).

- **Discovery:** an unpaired V1 advertises its hostname (`Mysa-<id>`) where
  other models advertise a model ID. 0.9.0 and later discover it.
- **Pairing:** some units reboot when the setup code is submitted. The reboot
  happens on the thermostat. One reporter paired after a full reset with their
  Wi-Fi temporarily 2.4 GHz-only; the steps are in
  [troubleshooting](guides/troubleshooting.md).
- **After pairing:** no reports yet.

## In-Floor

One report on `INF-V1-0`, firmware 3.17.6.5
([#32](https://github.com/SokolskyNikita/mysa-homeassistant/issues/32)).

- **Works:** mode changes over HomeKit, and °F setpoints since 0.9.0 rounds
  them to the step.
- **Light sensor:** thermostats whose HomeKit accessory advertises a light
  sensor get an Illuminance entity. It was reported on In-Floor
  ([mysa-client#29](https://github.com/SokolskyNikita/mysa-client/issues/29))
  and has not been checked on hardware.
- **Not exposed:** the floor-sensor reading.

## Mini-Split AC

Implemented from Mysa's app and tested with simulated responses; no owner has
reported on it.

- **Fan and swing:** the options the AC's remote offers for the current mode,
  hidden while the AC is off.
- **Temperature range:** the heating and cooling ranges and their step come
  from the remote (16–30 °C in whole degrees by default) when the capabilities
  give none. No target is offered in fan-only or dry mode.
- **Heat/cool (auto):** a single target set in Home Assistant's `heat_cool`
  mode is written as Mysa's auto target (`targetAuto.setpoint`), in the
  remote's auto range, as Mysa's app does. The card offers that target in
  heat/cool only once the cloud reports one; until then it shows none, as
  before. Derived from Mysa's app and a third-party project's report on two
  ACs; not tested here.
- **Not implemented:** toggle-only swing.

## Central HVAC

[Experimental](#experimental-models): controlled through Mysa's cloud only, and
untested on hardware.

- **Controls:** the advertised heat, cool, off and fan-only modes; heat/cool
  auto with separate low and high targets when enabled; temperature, humidity
  and equipment action; fan on/auto/off and native fan speeds when advertised;
  supported display controls.
- **Heat/cool:** Home Assistant's `heat_cool` mode is Mysa's auto. It takes
  both targets together and refuses a single one. Each target is checked
  against its own limits, reported lockouts and the minimum gap between them.
- **Activity:** heating, cooling or idle comes from the equipment stages or
  fresh telemetry the thermostat reports, never from the selected mode. With no
  such data the action is unknown.
- **Fan:** fan operation (on, auto, off) is separate from the optional fan
  speed. A circulation program set in Mysa's app shows as `runtime`; it can't
  be edited here, and choosing on, auto or off replaces it.
- **Unverified:** every command started from Home Assistant.

## Baseboard V3

[Experimental](#experimental-models): controlled through Mysa's cloud only.
One report on firmware 5.1.15 ([#30](https://github.com/SokolskyNikita/mysa-homeassistant/issues/30)).

- **Works:** import, temperature, humidity, mode, setpoint and heating action
  matched the Mysa app, and a setpoint changed in the app reached Home Assistant
  within one poll.
- **Controls:** heat/off and setpoint; screen units, brightness and button lock
  when advertised.
- **Fixed in 0.9.0:** a schedule-recovery warning logged every minute.
- **Unverified:** commands started from Home Assistant, display controls and
  behaviour during a cloud outage.

To help test one, follow [test a model nobody has reported on](guides/debugging.md#test-a-model-nobody-has-reported-on).

## Climate Sensor

[Experimental](#experimental-models): readings through Mysa's cloud only.
Temperature, humidity and connectivity, plus Wi-Fi signal when reported. It has
no climate entity or heating controls, and it is untested on hardware.

## Experimental models

Baseboard V3, Central HVAC and Climate Sensor are controlled through Mysa's
cloud only. Mysa Local has no local connection to them, so they need a Mysa
account and internet access, and they stop responding here when Mysa's cloud is
unreachable.

- **Adding one:** finish Wi-Fi setup, account enrollment and equipment
  configuration in the Mysa app. Then sign in to Mysa Local and import the
  device from your account. No HomeKit pairing or Matter setup is involved.
- **Controls come from the device's capabilities.** If Mysa returns no
  capability data, no default heating controls are created; entities can appear
  after a later refresh. Missing data is shown as unknown or unavailable.
- **A change is not confirmed by Mysa accepting it.** The displayed state
  changes only when the device reports the new value.
- **Not available on these models:** schedules and holds, runtime and energy,
  remote sensor linking, equipment configuration, emergency-heat controls,
  adaptive-display settings, demand-response enrollment and firmware updates.
  Use the Mysa app for those, and for removing a device.
- **Support was built from Mysa's app,** and tested against synthetic
  responses. See the [evidence](implementation/progress.md#newer-model-evidence).

## Things that apply to every model

- **Wi-Fi:** Mysa thermostats join 2.4 GHz networks.
- **Resetting:** on V1, V2 and Lite, holding the up and down arrows for 10
  seconds removes the thermostat's Wi-Fi, its HomeKit pairing, its schedule and
  its settings, and it must be set up again
  ([Mysa's instructions](https://help.getmysa.com/articles/2029638624-removing-resetting-baseboard)).
- **Fahrenheit:** the climate card and the setpoint limits work in whole °F;
  holds, schedules and the temperature offset are in °C. See
  [Fahrenheit](guides/usage.md#fahrenheit).
- **Controls depend on the device.** Entities are created from what the
  thermostat or its account reports, so a model can show fewer controls than
  listed here.
