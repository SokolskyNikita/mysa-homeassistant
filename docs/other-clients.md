# Other Mysa clients

Open-source projects by other people that talk to Mysa thermostats, checked on
2026-11-15. A project is listed if it has at least one star and a commit on its
main branch in the last five years. Forks with few changes, test repositories
and projects that only package another one's code without changes are left out.

Star counts and dates are from that day. Check each project's own model and
feature list before switching: Mysa Local's hardware testing is limited to the
Baseboard Lite, and other projects may expose controls it does not.

- [Actively maintained](#actively-maintained)
- [Other projects](#other-projects)
- [Not on this list](#not-on-this-list)

## Actively maintained

Projects with a commit on their main branch in the last 6 months that, in our
judgment, are the best kept up: recent releases or fixes, and maintainers who
answer issues. Ranked by stars.

| Project | Stars | What it is | How it reaches the thermostat | Last commit |
| --- | ---: | --- | --- | --- |
| [bourquep/mysa2mqtt](https://github.com/bourquep/mysa2mqtt) | 45 | Bridge that publishes thermostats to your own MQTT broker, with Home Assistant discovery. Runs on Node.js or Docker. The same repository holds `mysa-js-sdk`, a TypeScript library | Mysa's cloud, over MQTT with an HTTPS fallback | 2026-11-14 |
| [dlenski/mysotherm](https://github.com/dlenski/mysotherm) | 25 | Python command-line tool for inspecting thermostats, with detailed notes on Mysa's message formats | Mysa's cloud, over MQTT and HTTPS | 2026-11-04 |
| [homeCore-io/homeCore](https://github.com/homeCore-io/homeCore/tree/main/plugins/hc-mysa) | 4 | `hc-mysa`, a plugin for the homeCore home-automation hub, written in Rust | Mysa's [official Consumer API](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/guides/official-api.md), polled once a minute | 2026-11-15 |

## Other projects

Everything else that meets the listing rules, by stars.

| Project | Stars | What it is | How it reaches the thermostat | Last commit |
| --- | ---: | --- | --- | --- |
| [kgelinas/Mysa_HA](https://github.com/kgelinas/Mysa_HA) | 40 | Home Assistant custom integration. Lists Baseboard V1, V2 and Lite, In-Floor and Mini-Split AC as supported, with Central HVAC in testing. | Mysa's cloud, over MQTT and HTTPS | 2026-03-22 |
| [jdthomas/mysa_rs](https://github.com/jdthomas/mysa_rs) | 2 | Small Rust service that polls temperature and humidity and publishes them to MQTT. Read-only | Mysa's cloud, over HTTPS | 2024-01-07 |
| [itsamenathan/mysa2mqtt-hass-addon](https://github.com/itsamenathan/mysa2mqtt-hass-addon) | 1 | Home Assistant add-on that runs mysa2mqtt. It was behind mysa2mqtt's current version when checked | Same as mysa2mqtt | 2026-01-18 |
| [russell-corbett/mysa2ha](https://github.com/russell-corbett/mysa2ha) | 1 | Home Assistant custom integration for Baseboard V1, V2 and Lite and Mini-Split AC | Mysa's cloud, over MQTT and HTTPS | 2026-11-15 |

## Not on this list

- **Home Assistant's built-in
  [HomeKit Device](https://www.home-assistant.io/integrations/homekit_controller/)
  integration** controls a paired thermostat locally with no Mysa-specific
  code. The controls it offers depend on the services the thermostat exposes.
  The [setup guide](guides/setup.md) covers moving from it to Mysa Local.
- **Mysa's own examples** for the official Consumer API are at
  [empoweredhomes/mysa-api-examples](https://github.com/empoweredhomes/mysa-api-examples).
- **[fdurand/mysa-thermostats](https://github.com/fdurand/mysa-thermostats)**
  (8 stars) first showed how to sign in to Mysa's cloud. Its last commit is
  from December 2020, outside the five-year window.
- **[bourquep/mysa-js-sdk](https://github.com/bourquep/mysa-js-sdk)** (6 stars)
  is archived; the library now lives in the mysa2mqtt repository.
