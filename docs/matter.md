# Mysa Matter reference (stub)

Last reviewed: **2026-10-12**. This is a research placeholder based on vendor
documentation and standard Matter behavior. We have no Matter-capable Mysa
hardware, captured cluster/attribute dump, or hardware validation. Mysa Local
does not currently implement a Matter transport; its newer-model cloud support
does not establish local Matter support. See [HomeKit](homekit.md) for the
separately tested local protocol.

## Documented device support

| Model | Vendor requirement | Documented Matter surface |
| --- | --- | --- |
| Baseboard Gen3 | Firmware 5.1.4+, Mysa app 4.18+ | Current temperature, heating setpoint, Heat/Off; the Apple Home guide also describes humidity |
| Central HVAC | Firmware 4.5.37+, Mysa app 4.18+ | Current temperature, humidity, heating/cooling setpoints, Heat/Cool/Auto/Off; fan-only and auxiliary heat have platform-dependent presentation/control limits |

Sources: [Gen3 setup and controls](https://help.getmysa.com/articles/5681037454-Mysa-Baseboard-3%3A-Connecting-to-your-Smart-Home-using-Matter),
[Gen3 humidity](https://help.getmysa.com/articles/1174232297-baseboard-3-matter-pairing-apple-home),
and [Central HVAC controls](https://help.getmysa.com/articles/9079106768-Pairing-Mysa-with-Google-Home-via-Matter).
These are vendor claims, not observations from this repository. Do not extend
them to the tested Baseboard Lite or other older models.

## Expected local control path

1. Complete setup in the Mysa app, then obtain the Matter setup code from
   **Home Assistants**. This is separate from HomeKit pairing and cloud login.
2. Commission the device into Home Assistant's Matter controller. The expected
   path is HA's built-in Matter integration and Matter Server, communicating
   locally over Wi-Fi/IP after pairing. Matter credentials and controller
   identity are separate from this plugin's HomeKit keys and Cognito tokens.
3. Discover the device's endpoints, clusters, supported attributes and features.
   The documented controls likely map to standard Thermostat and Relative
   Humidity Measurement clusters. Exact endpoint IDs, optional attributes,
   write permissions and vendor extensions remain unknown.
4. Read/subscribe to reported state and write supported setpoints/modes. A future
   Mysa Local adapter would need an explicit integration design; the existing
   HomeKit adapter cannot be assumed to handle Matter devices.

This is an expected architecture, not an implemented or tested workflow here.
See [HA Matter documentation](https://www.home-assistant.io/integrations/matter/).
If already paired to another platform, Mysa documents using that platform's
Matter sharing code to add another controller:
[Gen3 multi-admin](https://help.getmysa.com/articles/2164862563-baseboard-3-matter-multiple-smart-home-platforms),
[Central HVAC multi-admin](https://help.getmysa.com/articles/2674671571-Using-Matter-with-Multiple-Smart-Home-Platforms).

## Unknowns and next evidence

Gen3 documentation explicitly excludes schedules, presets, heater-type settings,
temperature offset, geofencing and zoning from Matter. We have not established
power/energy readings, actual heating demand/running state, display units,
brightness, button lock, or manufacturer-specific extensions. Missing controls
in a platform UI do not prove that the device lacks corresponding attributes.

When volunteer hardware becomes available, record the exact model and firmware
and obtain a sanitized **Matter → device → three-dot menu → Download diagnostics**
export from HA. It includes cluster and attribute data:
[HA diagnostics instructions](https://www.home-assistant.io/integrations/matter/#downloading-diagnostics).
Use it to inventory capabilities before selecting an adapter design. Then verify
subscriptions, supported writes and independent readback, reconnect behavior,
and operation without cloud access. Keep those observations separate from vendor
claims and inferred standard-cluster mappings.
