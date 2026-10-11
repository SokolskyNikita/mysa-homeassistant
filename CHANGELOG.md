# Changelog

## 0.1.0 (2026-10-11)

First release. Tested on a Mysa Baseboard Lite (BB-V2-0-L), firmware 3.17.5.13.

- Local control over HomeKit: setpoint, mode, room temperature, real heating state, display units and Identify. HomeKit discovery of Mysa models is claimed by this integration, and pairing takes one step.
- Move a thermostat over from the HomeKit Device integration without its setup code. Entity IDs, names and areas are kept, and the old entry is removed.
- Optional Mysa sign-in (Cognito SRP; only a refresh token is stored) adds button lock, wake and sleep brightness, hardware setpoint limits, temperature offset, heater type, firmware check, cloud connectivity, Wi-Fi signal and the thermostat's own heating runtime.
- Local-first routing: changes go over HomeKit and fall back to the cloud, with a read-back after a timed-out write so nothing is applied twice. A connection-path sensor shows the active path, and a repair issue appears after 15 minutes without the local path.
- While a thermostat runs on the cloud, Mysa's live mode is requested and the poll speeds up to 10 s. Both stop as soon as the local connection is back.
- One cloud request polls all thermostats. Polling backs off on errors and rate limits.
- A cloud off switch makes zero calls to Mysa's servers.
- Heating runtime today (from the thermostat, or counted locally) and heater energy today from a wattage you set, ready for the Energy dashboard.
- Actions: `hold`, `resume_schedule`, `set_schedule`, `clear_schedule`, plus a schedule-hold sensor.
- Firmware update entity: checking is always on, and installing is opt-in under Advanced options.
- Diagnostics with redaction, translations and brand images.
