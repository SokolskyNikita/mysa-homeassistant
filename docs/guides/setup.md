# Set up and recover Mysa Local

[Documentation index](../README.md)

## Setup

### Pair over your local network

1. Connect the thermostat to 2.4 GHz Wi-Fi. You can use the Mysa app or the separate [Mysa Wi-Fi setup page](https://sokolskynikita.github.io/mysa-wifi-setup/). The page uses Bluetooth in Chrome or Edge without the Mysa app or an account. A new or reset thermostat shows a pairing symbol, usually a Bluetooth icon.
2. Press a button on the thermostat to wake its HomeKit service. In Settings → Devices & services, add the discovered Mysa. You can also choose Add integration → Mysa Local → Pair a thermostat on this network.
3. Enter the eight-digit HomeKit code printed on the back of the faceplate. Spaces and dashes are accepted.
4. Sign in to Mysa for cloud features, or choose Skip, local only.

Home Assistant must be able to reach the thermostat and discover it through mDNS. If it is paired to Apple Home or another controller, remove that pairing before starting a new one. For an existing Home Assistant HomeKit Device entry, use the migration below.

To add another thermostat, use its discovery card or Add thermostat on the Mysa Local integration page.

Pairing credentials are saved as soon as the thermostat accepts them. If setup is interrupted, start it again and choose the saved pairing to continue without another HomeKit code. See [pairing recovery](troubleshooting.md#pairing-and-discovery) if the saved session cannot connect.

### Move from HomeKit Device

Choose Move a thermostat from the HomeKit Device integration during setup or when adding a thermostat. The existing pairing must be reachable. Mysa Local uses it to add its own pairing, so the setup code is not required.

The migration transfers matching climate, temperature, display-units and Identify entities with their IDs and registry settings, unless an equivalent Mysa Local entity already exists. It also copies the device's name, area and labels, then removes the old HomeKit Device entry.

### Add thermostats from your Mysa account

Sign in during setup or through the integration's options, then select thermostats from the account. For models with HomeKit, you can pair each one locally with its code or leave it under cloud control. V3, Central HVAC and Climate Sensor use cloud control here. A thermostat without local pairing needs Mysa's servers and an internet connection to work.

Local pairing and Mysa account membership are separate. While signed in, Mysa Local offers to add a locally paired thermostat to the account if it is missing there. If the account has no home, create one in the Mysa app first.

Repairs notices identify thermostats that are only on the cloud, missing from the account or removed from it after setup. You can use the offered fix or ignore a notice when that arrangement is intentional.

## Remembering setup codes

When pairing, optionally select **Remember HomeKit setup code**. This is
recommended for some flaky devices that may need pairing again. Only a code
accepted by the accessory is saved, in private local Home Assistant storage,
separately from the cryptographic pairing keys. It is excluded from integration
diagnostics. Remembering a code does not enable automatic re-pairing.

To delete saved codes, open the integration's main configuration menu, select
**Forget remembered HomeKit codes**, and confirm. This deletes all remembered
codes without removing existing pairings or disconnecting thermostats. The menu
item appears only when codes have been saved. Removing a thermostat also forgets
its code. Deleting saved codes does not remove copies in older HA backups.

For cloud credentials and account controls, see [saved sign-in](usage.md#saved-sign-in) and [per-thermostat settings](usage.md#per-thermostat-settings).
