# Mysa HomeKit accessory reference

The maintained accessory profile, characteristic findings, and hardware
evidence now lives in the standalone client repository:
[Mysa HomeKit accessory reference](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/homekit.md).
Mysa Local implementation parity and deferred work remain in the
[HomeKit tracker](implementation/homekit.md).

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

## Characteristic coverage

See the maintained reference for the observed Lite profile and its limits.

## HomeKit Wi-Fi switching

See the maintained reference for the staged Wi-Fi configuration experiment and
why acceptance did not establish a successful network migration.

## Moved sections

These links preserve the original section bookmarks.

| Maintained client reference |
| --- |
| <a id="discovery-and-pairing"></a>[Discovery and pairing](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/homekit.md#discovery-and-pairing) |
| <a id="implementation-and-validation-limits"></a>[Implementation and validation limits](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/homekit.md#implementation-and-validation-limits) |
