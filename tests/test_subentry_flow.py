"""Thermostat subentry setup and management flows."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr, issue_registry as ir
import pytest

from custom_components.mysa_local.const import (
    CONF_CLOUD_RUNTIME,
    CONF_HEATER_WATTS,
    CONF_MODEL,
    CONF_MQTT_ENABLED,
    CONF_PAIRING,
    DOMAIN,
    SUBENTRY_THERMOSTAT,
)

from .common import (
    ACCOUNT_ISSUE,
    DEVICE_ID,
    HKID,
    PAIRING_CODE,
    FakeCloud,
    cloud_only_subentry,
    hub_entry,
    thermostat_subentry,
)
from .conftest import setup_entry


@pytest.mark.usefixtures("controller")
async def test_subentry_reconfigure_wattage(hass: HomeAssistant) -> None:
    """Setting a wattage adds the energy sensor."""
    entry = hub_entry(signed_in=False)
    await setup_entry(hass, entry)
    assert hass.states.get("sensor.living_room_heater_energy_today") is None
    (subentry,) = entry.subentries.values()

    result = await entry.start_subentry_reconfigure_flow(hass, subentry.subentry_id)
    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "reconfigure"
    # Signed out: nothing to do with the account; it's paired already.
    assert result["menu_options"] == ["settings"]
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"next_step_id": "settings"}
    )
    assert result["step_id"] == "settings"
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {CONF_HEATER_WATTS: 1500, CONF_CLOUD_RUNTIME: False}
    )
    assert result["reason"] == "reconfigure_successful"
    await hass.async_block_till_done(wait_background_tasks=True)
    assert subentry.data[CONF_HEATER_WATTS] == 1500
    assert hass.states.get("sensor.living_room_heater_energy_today").state == "0.0"
    power = hass.states.get("sensor.living_room_heater_power")
    assert power.attributes["unit_of_measurement"] == "W"
    assert power.state in ("0.0", "1500.0")  # follows the relay; see test_features


@pytest.mark.usefixtures("controller")
async def test_subentry_settings_toggle_mqtt_and_preserve_saved_choice(
    hass: HomeAssistant,
) -> None:
    """Per-device MQTT is shown and remains unchanged in later settings edits."""
    entry = hub_entry(signed_in=False)
    await setup_entry(hass, entry)
    (subentry,) = entry.subentries.values()
    assert subentry.data.get(CONF_MQTT_ENABLED, True) is True

    async def open_settings() -> dict:
        result = await entry.start_subentry_reconfigure_flow(hass, subentry.subentry_id)
        return await hass.config_entries.subentries.async_configure(
            result["flow_id"], {"next_step_id": "settings"}
        )

    result = await open_settings()
    schema_fields = {
        getattr(field, "schema", field) for field in result["data_schema"].schema
    }
    assert CONF_MQTT_ENABLED in schema_fields
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            CONF_HEATER_WATTS: 0,
            CONF_CLOUD_RUNTIME: True,
            CONF_MQTT_ENABLED: False,
        },
    )
    assert result["reason"] == "reconfigure_successful"
    await hass.async_block_till_done(wait_background_tasks=True)
    assert subentry.data[CONF_MQTT_ENABLED] is False

    result = await open_settings()
    await hass.config_entries.subentries.async_configure(
        result["flow_id"], {CONF_HEATER_WATTS: 0, CONF_CLOUD_RUNTIME: True}
    )
    await hass.async_block_till_done(wait_background_tasks=True)
    assert subentry.data[CONF_MQTT_ENABLED] is False


@pytest.mark.usefixtures("unpaired_controller")
async def test_subentry_add_by_pairing(hass: HomeAssistant) -> None:
    """Add thermostat from the integration page."""
    entry = hub_entry(signed_in=False, subentries=[])
    entry.add_to_hass(hass)
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_THERMOSTAT),
        context={"source": config_entries.SOURCE_USER},
    )
    assert result["type"] is FlowResultType.MENU
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"next_step_id": "pick"}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"device": HKID.lower()}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"pairing_code": PAIRING_CODE}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    (subentry,) = entry.subentries.values()
    assert subentry.unique_id == DEVICE_ID
    assert subentry.data[CONF_MQTT_ENABLED] is True


@pytest.mark.usefixtures("unpaired_controller")
async def test_add_thermostat_offers_account(
    hass: HomeAssistant, cloud: FakeCloud
) -> None:
    """Add thermostat > pair: offered to put it on the account."""
    cloud.remove_from_account()
    entry = hub_entry(subentries=[])
    await setup_entry(hass, entry)
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "thermostat"), context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"next_step_id": "pick"}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"device": HKID.lower()}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"pairing_code": PAIRING_CODE}
    )
    assert result["step_id"] == "account"
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"add_to_account": True}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert [c["Id"] for c in cloud.claims] == [DEVICE_ID]
    await hass.async_block_till_done(wait_background_tasks=True)


@pytest.mark.usefixtures("unpaired_controller")
async def test_add_thermostat_on_account_skips_offer(
    hass: HomeAssistant, cloud: FakeCloud
) -> None:
    """Already on the account: no question asked."""
    entry = hub_entry(subentries=[])
    await setup_entry(hass, entry)
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "thermostat"), context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"next_step_id": "pick"}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"device": HKID.lower()}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"pairing_code": PAIRING_CODE}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert cloud.claims == []
    await hass.async_block_till_done(wait_background_tasks=True)


@pytest.mark.usefixtures("controller")
async def test_add_from_account_not_found_then_skip(
    hass: HomeAssistant, cloud: FakeCloud
) -> None:
    """Not on the network (dozing): try again or skip; skip adds it cloud only."""
    entry = hub_entry(subentries=[])
    await setup_entry(hass, entry)
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "thermostat"), context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"next_step_id": "cloud_pick"}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"device": DEVICE_ID}
    )
    assert result["step_id"] == "local_offer"
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"next_step_id": "local_find"}
    )
    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "local_not_found"
    assert result["description_placeholders"]["homekit_name"] == "Mysa-d4e5f6"
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"next_step_id": "local_skip"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert CONF_PAIRING not in result["data"]


@pytest.mark.usefixtures("controller")
async def test_settings_remove_and_add_back(
    hass: HomeAssistant, cloud: FakeCloud
) -> None:
    """Remove from the account (with the app's cleanup), then add back."""
    cloud.scenes[1]["DeviceActions"] = {DEVICE_ID: {"setpoint": 18}, "other": {}}
    cloud.schedules = [{"id": "s1", "deviceIds": [DEVICE_ID], "events": []}]
    entry = hub_entry()
    await setup_entry(hass, entry)
    (subentry,) = entry.subentries.values()

    result = await entry.start_subentry_reconfigure_flow(hass, subentry.subentry_id)
    assert result["menu_options"] == ["settings", "account_remove"]
    assert (
        (result["description_placeholders"]["account"])
        == "✅ **Mysa account:** on your account"
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"next_step_id": "account_remove"}
    )
    assert result["step_id"] == "account_remove"
    result = await hass.config_entries.subentries.async_configure(result["flow_id"], {})
    assert result["reason"] == "removed_from_account"
    assert cloud.deleted == [DEVICE_ID]
    assert cloud.schedules == []
    assert cloud.shortcut_updates == [
        (cloud.scenes[1]["Id"], {"DeviceActions": {"other": {}}, "AllDevices": True})
    ]
    await hass.async_block_till_done(wait_background_tasks=True)
    assert hass.states.get("sensor.living_room_mysa_account").state == "not_on_account"
    issue = ir.async_get(hass).async_get_issue(DOMAIN, ACCOUNT_ISSUE)
    assert issue is not None
    assert issue.translation_key == "not_on_account"

    result = await entry.start_subentry_reconfigure_flow(hass, subentry.subentry_id)
    assert result["menu_options"] == ["settings", "account_add"]
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"next_step_id": "account_add"}
    )
    assert result["step_id"] == "account"
    assert "add_to_account" not in result["data_schema"].schema
    result = await hass.config_entries.subentries.async_configure(result["flow_id"], {})
    assert result["reason"] == "added_to_account"
    assert cloud.claims[-1]["Name"] == "Living Room"
    await hass.async_block_till_done(wait_background_tasks=True)
    assert hass.states.get("sensor.living_room_mysa_account").state == "on_account"
    assert ir.async_get(hass).async_get_issue(DOMAIN, ACCOUNT_ISSUE) is None


@pytest.mark.usefixtures("unpaired_controller")
async def test_settings_pair_cloud_only_locally(
    hass: HomeAssistant, cloud: FakeCloud
) -> None:
    """A cloud-only thermostat gets paired from its settings."""
    unpaired_subentry = cloud_only_subentry()
    unpaired_subentry["data"][CONF_MQTT_ENABLED] = False
    entry = hub_entry(subentries=[unpaired_subentry])
    await setup_entry(hass, entry)
    assert hass.states.get("sensor.living_room_homekit_pairing").state == "not_paired"
    (subentry,) = entry.subentries.values()
    result = await entry.start_subentry_reconfigure_flow(hass, subentry.subentry_id)
    assert result["menu_options"] == ["settings", "local_pair", "account_remove"]
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"next_step_id": "local_pair"}
    )
    assert result["step_id"] == "pair"
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"pairing_code": PAIRING_CODE}
    )
    assert result["reason"] == "thermostat_updated"
    await hass.async_block_till_done(wait_background_tasks=True)
    assert entry.subentries[subentry.subentry_id].data[CONF_PAIRING]
    assert entry.subentries[subentry.subentry_id].data[CONF_MQTT_ENABLED] is False


@pytest.mark.usefixtures("unpaired_controller")
async def test_pairing_a_different_thermostat_is_undone(
    hass: HomeAssistant, cloud: FakeCloud
) -> None:
    """If the code pairs another Mysa, that pairing is removed again."""
    entry = hub_entry(subentries=[cloud_only_subentry("ffffffd4e5f6")])
    await setup_entry(hass, entry)
    (subentry,) = entry.subentries.values()
    result = await entry.start_subentry_reconfigure_flow(hass, subentry.subentry_id)
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"next_step_id": "local_pair"}
    )
    with patch(
        "custom_components.mysa_local.flows.thermostat.async_unpair", AsyncMock()
    ) as unpair:
        result = await hass.config_entries.subentries.async_configure(
            result["flow_id"], {"pairing_code": PAIRING_CODE}
        )
    assert result["reason"] == "wrong_thermostat"
    unpair.assert_awaited_once()
    assert CONF_PAIRING not in entry.subentries[subentry.subentry_id].data


@pytest.mark.usefixtures("controller")
async def test_add_from_account_includes_new_models(
    hass: HomeAssistant, cloud: FakeCloud
) -> None:
    """New cloud models remain available when existing local models are imported."""
    cloud.add_unsupported()
    entry = hub_entry()
    await setup_entry(hass, entry)
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "thermostat"), context={"source": config_entries.SOURCE_USER}
    )
    assert "cloud_pick" in result["menu_options"]


@pytest.mark.usefixtures("controller")
async def test_cloud_only_model_can_disable_per_device_mqtt(
    hass: HomeAssistant, cloud: FakeCloud
) -> None:
    """Experimental cloud-only models expose MQTT without heater settings."""
    device_id = cloud.add_unsupported()
    subentry = cloud_only_subentry(device_id)
    subentry["data"][CONF_MODEL] = "BB-V3-0"
    entry = hub_entry(subentries=[subentry])
    await setup_entry(hass, entry)
    (configured,) = entry.subentries.values()

    result = await entry.start_subentry_reconfigure_flow(hass, configured.subentry_id)
    assert result["menu_options"] == ["cloud_info", "mqtt"]
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"next_step_id": "mqtt"}
    )
    assert result["step_id"] == "mqtt"
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {CONF_MQTT_ENABLED: False}
    )
    assert result["reason"] == "reconfigure_successful"
    await hass.async_block_till_done(wait_background_tasks=True)
    assert configured.data[CONF_MQTT_ENABLED] is False


@pytest.mark.usefixtures("controller")
async def test_names_shown_and_synced_both_ways(
    hass: HomeAssistant, cloud: FakeCloud
) -> None:
    """Different names: the model line shows the Mysa name; rename either way."""
    sub = thermostat_subentry()
    sub["title"] = "Mysa-d4e5f6"
    entry = hub_entry(subentries=[sub])
    await setup_entry(hass, entry)
    device = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, DEVICE_ID), entry.entry_id
    )
    assert device is not None
    assert device.model == (
        f"Mysa Baseboard Lite · Mysa ID {DEVICE_ID} · Mysa app: Living Room"
    )
    (subentry,) = entry.subentries.values()

    result = await entry.start_subentry_reconfigure_flow(hass, subentry.subentry_id)
    assert result["menu_options"] == [
        "settings",
        "name_from_mysa",
        "name_to_mysa",
        "account_remove",
    ]
    assert (
        'named "Living Room" in the Mysa app'
        in (result["description_placeholders"]["account"])
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"next_step_id": "name_to_mysa"}
    )
    assert result["step_id"] == "name_to_mysa"
    result = await hass.config_entries.subentries.async_configure(result["flow_id"], {})
    assert result["reason"] == "renamed_in_mysa"
    assert cloud.settings == [{"Name": "Mysa-d4e5f6"}]
    await entry.runtime_data.cloud.async_refresh()
    await hass.async_block_till_done(wait_background_tasks=True)
    device = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, DEVICE_ID), entry.entry_id
    )
    assert device is not None
    assert device.model == f"Mysa Baseboard Lite · Mysa ID {DEVICE_ID}"

    # Back to the Mysa name, then use it in Home Assistant.
    cloud.devices["DevicesObj"][DEVICE_ID]["Name"] = "Living Room"
    await entry.runtime_data.cloud.async_refresh()
    entry.runtime_data.cloud.mark_stale("records")
    await entry.runtime_data.cloud.async_refresh()
    result = await entry.start_subentry_reconfigure_flow(hass, subentry.subentry_id)
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"next_step_id": "name_from_mysa"}
    )
    assert result["step_id"] == "name_from_mysa"
    result = await hass.config_entries.subentries.async_configure(result["flow_id"], {})
    assert result["reason"] == "renamed_here"
    await hass.async_block_till_done(wait_background_tasks=True)
    assert entry.subentries[subentry.subentry_id].title == "Living Room"
    device = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, DEVICE_ID), entry.entry_id
    )
    assert device is not None
    assert device.name == "Living Room"
    assert device.model == f"Mysa Baseboard Lite · Mysa ID {DEVICE_ID}"


@pytest.mark.usefixtures("controller")
@pytest.mark.parametrize(
    ("status", "error"),
    [(403, "claim_refused"), (503, "cannot_connect"), (400, "claim_failed")],
)
async def test_account_step_errors(
    hass: HomeAssistant, cloud: FakeCloud, status: int, error: str
) -> None:
    """Mysa refusing or failing keeps the form open; a retry can succeed."""
    cloud.remove_from_account()
    entry = hub_entry()
    await setup_entry(hass, entry)
    (subentry,) = entry.subentries.values()
    result = await entry.start_subentry_reconfigure_flow(hass, subentry.subentry_id)
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"next_step_id": "account_add"}
    )
    cloud.claim_status = status
    result = await hass.config_entries.subentries.async_configure(result["flow_id"], {})
    assert result["errors"] == {"base": error}
    cloud.claim_status = None
    result = await hass.config_entries.subentries.async_configure(result["flow_id"], {})
    assert result["reason"] == "added_to_account"


@pytest.mark.usefixtures("controller")
async def test_account_step_without_a_home(
    hass: HomeAssistant, cloud: FakeCloud
) -> None:
    """An account with no home can't take thermostats."""
    cloud.remove_from_account()
    cloud.bootstrap["data"]["homes"] = []
    cloud.bootstrap["data"]["user"]["AllowedHomes"] = []
    entry = hub_entry()
    await setup_entry(hass, entry)
    (subentry,) = entry.subentries.values()
    result = await entry.start_subentry_reconfigure_flow(hass, subentry.subentry_id)
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"next_step_id": "account_add"}
    )
    assert result["errors"] == {"base": "no_home"}
    result = await hass.config_entries.subentries.async_configure(result["flow_id"], {})
    assert result["errors"] == {"base": "no_home"}
    assert cloud.claims == []
