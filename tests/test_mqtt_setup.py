"""MQTT per-device allowlisting and entry shutdown wiring."""

from copy import deepcopy
from unittest.mock import AsyncMock, patch

from homeassistant.core import HomeAssistant
from mysa_client.cloud.const import API_BASE
import pytest

from custom_components.mysa_local.const import (
    CONF_CLOUD_ENABLED,
    CONF_CLOUD_RUNTIME,
    CONF_DEVICE_ID,
    CONF_HEATER_WATTS,
    CONF_MQTT_ENABLED,
)

from .common import DEVICE_ID, FakeCloud, hub_entry, thermostat_subentry
from .conftest import setup_entry

SECOND_DEVICE_ID = "b2c3d4e5f607"


def _add_account_device(cloud: FakeCloud, device_id: str, name: str) -> None:
    """Add a second Lite to the fake account catalog."""
    record = deepcopy(cloud.devices["DevicesObj"][DEVICE_ID])
    record.update({"Id": device_id, "Name": name})
    cloud.devices["DevicesObj"][device_id] = record
    cloud.bootstrap["data"]["devices"][device_id] = deepcopy(record)
    cloud.bootstrap["data"]["user"]["AllowedDevices"].append(device_id)
    cloud.mock.get(f"{API_BASE}/capabilities/{device_id}", json=cloud.capabilities)
    cloud.mock.get(
        f"{API_BASE}/devices/update_available/{device_id}", json=cloud.firmware
    )
    cloud.mock.get(f"{API_BASE}/schedule/hold/{device_id}", side_effect=cloud._get_hold)
    cloud.mock.post(
        f"{API_BASE}/telemetry/usage/{device_id}",
        side_effect=cloud._json(lambda: cloud.usage),
    )


def _subentry_for(device_id: str, title: str) -> dict:
    """Build another cloud-only thermostat subentry for the setup tests."""
    subentry = thermostat_subentry(pairing=None)
    subentry["data"][CONF_DEVICE_ID] = device_id
    subentry["title"] = title
    subentry["unique_id"] = device_id
    return subentry


async def test_account_entry_starts_mqtt_for_only_configured_account_devices(
    hass: HomeAssistant, cloud: FakeCloud
) -> None:
    """An old configured account device defaults to MQTT on."""
    entry = hub_entry(subentries=[thermostat_subentry(pairing=None)])
    with patch(
        "custom_components.mysa_local.mqtt_coordinator.MysaMqttCoordinator.async_start"
    ) as start:
        await setup_entry(hass, entry)
    mqtt = entry.runtime_data.mqtt
    assert mqtt is not None
    start.assert_called_once()
    assert mqtt._devices == {DEVICE_ID: "BB-V2-0-L"}
    assert next(iter(entry.runtime_data.thermostats.values())).mqtt is mqtt
    with patch.object(mqtt, "async_stop", new_callable=AsyncMock) as stop:
        assert await hass.config_entries.async_unload(entry.entry_id)
    stop.assert_awaited_once()


async def test_mqtt_allowlist_supports_mixed_per_device_choices(
    hass: HomeAssistant, cloud: FakeCloud
) -> None:
    """The missing setting defaults on while an explicitly disabled device is omitted."""
    _add_account_device(cloud, SECOND_DEVICE_ID, "Bedroom")
    disabled = _subentry_for(SECOND_DEVICE_ID, "Bedroom")
    disabled["data"][CONF_MQTT_ENABLED] = False
    entry = hub_entry(subentries=[thermostat_subentry(pairing=None), disabled])

    with patch(
        "custom_components.mysa_local.mqtt_coordinator.MysaMqttCoordinator.async_start"
    ):
        await setup_entry(hass, entry)

    assert entry.runtime_data.mqtt is not None
    assert entry.runtime_data.mqtt._devices == {DEVICE_ID: "BB-V2-0-L"}


async def test_mqtt_allowlist_includes_all_devices_when_enabled(
    hass: HomeAssistant, cloud: FakeCloud
) -> None:
    """Two enabled account devices are both subscribed."""
    _add_account_device(cloud, SECOND_DEVICE_ID, "Bedroom")
    entry = hub_entry(
        subentries=[
            thermostat_subentry(pairing=None),
            _subentry_for(SECOND_DEVICE_ID, "Bedroom"),
        ]
    )

    with patch(
        "custom_components.mysa_local.mqtt_coordinator.MysaMqttCoordinator.async_start"
    ):
        await setup_entry(hass, entry)

    assert entry.runtime_data.mqtt is not None
    assert entry.runtime_data.mqtt._devices == {
        DEVICE_ID: "BB-V2-0-L",
        SECOND_DEVICE_ID: "BB-V2-0-L",
    }


async def test_mqtt_allowlist_is_empty_when_every_device_is_disabled(
    hass: HomeAssistant, cloud: FakeCloud
) -> None:
    """Disabling MQTT on every thermostat leaves the account coordinator idle."""
    _add_account_device(cloud, SECOND_DEVICE_ID, "Bedroom")
    first = thermostat_subentry(pairing=None)
    first["data"][CONF_MQTT_ENABLED] = False
    second = _subentry_for(SECOND_DEVICE_ID, "Bedroom")
    second["data"][CONF_MQTT_ENABLED] = False
    entry = hub_entry(subentries=[first, second])

    with patch(
        "custom_components.mysa_local.mqtt_coordinator.MysaMqttCoordinator.async_start"
    ):
        await setup_entry(hass, entry)

    assert entry.runtime_data.mqtt is not None
    assert entry.runtime_data.mqtt._devices == {}


async def test_device_mqtt_toggle_survives_config_entry_reload(
    hass: HomeAssistant, cloud: FakeCloud
) -> None:
    """Saving the per-device setting reloads MQTT with the new allowlist."""
    entry = hub_entry(subentries=[thermostat_subentry(pairing=None)])
    with patch(
        "custom_components.mysa_local.mqtt_coordinator.MysaMqttCoordinator.async_start"
    ):
        await setup_entry(hass, entry)
        (subentry,) = entry.subentries.values()
        result = await entry.start_subentry_reconfigure_flow(hass, subentry.subentry_id)
        result = await hass.config_entries.subentries.async_configure(
            result["flow_id"], {"next_step_id": "settings"}
        )
        await hass.config_entries.subentries.async_configure(
            result["flow_id"],
            {
                CONF_HEATER_WATTS: 0,
                CONF_CLOUD_RUNTIME: True,
                CONF_MQTT_ENABLED: False,
            },
        )
        await hass.async_block_till_done()

        assert subentry.data[CONF_MQTT_ENABLED] is False
        assert entry.runtime_data.mqtt is not None
        assert entry.runtime_data.mqtt._devices == {}
        assert await hass.config_entries.async_reload(entry.entry_id)
        assert entry.runtime_data.mqtt is not None
        assert entry.runtime_data.mqtt._devices == {}


@pytest.mark.parametrize(
    "options", [{CONF_MQTT_ENABLED: False}, {CONF_CLOUD_ENABLED: False}]
)
async def test_mqtt_respects_its_option_and_cloud_master_switch(
    hass: HomeAssistant, cloud: FakeCloud, options: dict[str, bool]
) -> None:
    """Disabling MQTT or all cloud access creates no MQTT coordinator."""
    entry = hub_entry(subentries=[thermostat_subentry(pairing=None)], options=options)
    await setup_entry(hass, entry)
    assert entry.runtime_data.mqtt is None


async def test_mqtt_removes_devices_no_longer_on_the_account(
    hass: HomeAssistant, cloud: FakeCloud
) -> None:
    """A cloud account refresh removes the subscription allowlist immediately."""
    entry = hub_entry(subentries=[thermostat_subentry(pairing=None)])
    await setup_entry(hass, entry)
    runtime = entry.runtime_data
    assert runtime.mqtt is not None
    assert runtime.cloud is not None
    assert runtime.cloud.data is not None
    runtime.cloud.data.records.clear()
    runtime.cloud.async_update_listeners()
    assert runtime.mqtt._devices == {}
