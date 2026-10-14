# Local control, cloud fallback, and reading freshness

[Documentation index](../README.md)

## Local and cloud behavior

HomeKit supplies push updates and a health check runs every 30 seconds. While the local connection is healthy, HomeKit provides the climate entity's control state and temperature. MQTT can independently supply live humidity and numeric heating duty cycle; see the [MQTT guide](../mqtt.md) for source selection and fallback. MQTT does not carry commands.

Setpoint, screen-unit and HomeKit-supported mode changes try HomeKit first. Modes without a HomeKit equivalent, including fan-only and dry, go directly through the cloud. A local write has a five-second timeout. After a timeout, the integration reads back the local value before retrying through the cloud. For setpoint and mode changes, it also checks cloud-reported state. A device rejection is reported directly.

## Reading freshness

Cloud state uses one batch request for all thermostats per poll. Settings records, capabilities and other data refresh less often. Outside live mode, a thermostat samples about every 30 seconds but uploads the samples to Mysa in batches, so the latest cloud reading is often 15–30 minutes old even while the thermostat is connected and accepts commands. Cloud climate fallback requires an explicit online status and a fresh room-temperature timestamp. Cloud measurements also use their own field timestamps. Outside confirmed live reporting, the maximum age is **35 minutes**, with at most one minute of future clock skew. Once telemetry confirms live reporting, the window tightens to the greater of **90 seconds** or **three cloud poll intervals**, while that live session remains valid. Settings controls remain available when the cloud responds and reports the thermostat online; they do not depend on the temperature-report timestamp. Local climate control is unaffected by stale cloud telemetry.

With Live mode during local outages enabled (the default), a lost LAN connection triggers a request for readings about every five seconds and changes cloud polling to ten seconds. Normal polling resumes when local control returns. This option applies to thermostats with a local pairing; thermostats added only from the account use the normal poll interval.

The Connection path sensor shows the active route. If a paired thermostat's LAN path stays down for 15 minutes, a repair notice appears and clears after recovery. Cloud errors and rate limits cause polling to back off; writes are spaced apart.

MQTT has its own receipt-time freshness window and per-field fallback, documented in [MQTT values and fallback](../mqtt.md#values-and-fallback). To investigate unavailable readings, see [troubleshooting](troubleshooting.md).

For maintenance, the integration supplies these windows in [measurement_max_age](../../custom_components/mysa_local/cloud_coordinator.py#L521); the default comes from the pinned client dependency.
