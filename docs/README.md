# Mysa Local documentation

Use the [installation and user guide](../README.md) to set up Mysa Local.
The [architecture guide](architecture.md) explains polling, local-first routing,
fallback, persistence, and ownership of integration behavior.

## Protocol map

| Path | Transport and identity | Reference |
| --- | --- | --- |
| HA/controller → thermostat | Local HomeKit over IP; saved controller pairing keys | [Accessory behavior](homekit.md), [implementation parity](implementation/homekit.md), [adapter API/lifecycle](../custom_components/mysa_local/protocols/homekit/README.md) |
| Setup client → thermostat | Bonded BLE; separate app-derived SoftAP HTTP flow | [Bluetooth and provisioning](bluetooth.md) |
| Current app/client → cloud | HTTPS with a Cognito ID token | [Primary cloud reference](../custom_components/mysa_local/protocols/cloud/README.md) |
| Legacy app/client → cloud | MQTT over secure WebSocket; temporary Cognito Identity Pool AWS credentials | [MQTT protocol and permissions](mqtt.md) |
| Thermostat → cloud | Device's own MQTT/TLS and firmware HTTPS connections | [Firmware and connectivity](firmware-and-connectivity.md) |

Wi-Fi joining, cloud enrollment, HomeKit pairing, and newer-model credential
provisioning are separate stages. App AWS credentials are not thermostat
credentials. S/R/L in the cloud reference distinguish structured services and
two entry points to legacy REST; hostname alone is not an API version.

## Cloud supporting references

- [Authentication](cloud-auth.md): Cognito wire exchange and browser constraints.
- [State, history, and model fields](cloud-state.md): discovery shapes, units,
  enum namespaces, telemetry, AC controls, and hardware limits.
- [Account and schedule workflows](cloud-workflows.md): claims/removal, schedules,
  holds, shortcuts, and app-only route context.
- [Implementation status and plan](implementation/cloud.md): the single cloud
  backlog, stable item IDs, completion criteria, dependencies, and exclusions.
- [JSON examples](examples/README.md): request/response and MQTT formats with
  provenance; these are documentation data and are never replayed by checks.

## Evidence and maintenance

Live evidence is strongest for two Baseboard Lites (`BB-V2-0-L`, firmware
`3.17.5.13`) observed October 10–12, 2026. Other legacy models have code or
app-derived mappings; AC, In-floor, and newer-model behavior must not be presented
as equivalently hardware-tested. Additional model validation follows community
volunteers testing their own hardware, with no fixed model priority. The BLE setup page is a [separate product](https://github.com/SokolskyNikita/mysa-wifi-setup).

Distinguish **API accepted**, **record confirmed**, **device reported**,
**locally confirmed**, and **end-to-end verified**. A false feature flag, hidden
app UI, accepted request, stored record, and working physical feature mean
different things. “Not observed” does not mean “unsupported.”

The cloud README owns protocol conclusions and recommended client policy;
code/tests and the tracker describe current implementation. The protocols were
reverse-engineered from the original Mysa apps. See [documentation maintenance](maintenance.md),
and [release history](../CHANGELOG.md). Validation limits remain beside the
relevant protocol findings.

- [Experimental newer-model cloud support and V3 test guide](new-cloud-models.md)
