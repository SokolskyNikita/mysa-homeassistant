"""Linking local and cloud: account offers, account changes, warnings, fixes."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

from homeassistant import config_entries
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr, issue_registry as ir
from homeassistant.setup import async_setup_component
import pytest

from custom_components.mysa_local.const import CONF_DEVICE_ID, CONF_PAIRING, DOMAIN

from .common import (
    DEVICE_ID,
    EMAIL,
    HKID,
    HOME_ID,
    PAIRING_CODE,
    PASSWORD,
    FakeCloud,
    hub_entry,
    thermostat_subentry,
)
from .conftest import setup_entry
from .test_config_flow import discovery

ACCOUNT_ISSUE = f"not_on_account_{DEVICE_ID}"
LOCAL_ISSUE = f"cloud_only_{DEVICE_ID}"


def cloud_only_subentry(device_id: str = DEVICE_ID) -> dict[str, Any]:
    """A thermostat added from the account, not paired locally."""
    sub = thermostat_subentry(pairing=None)
    sub["data"][CONF_DEVICE_ID] = device_id
    sub["unique_id"] = device_id
    return sub


async def fix_flow(hass: HomeAssistant, issue_id: str) -> tuple[Any, dict[str, Any]]:
    """Start the repair flow of an issue."""
    assert await async_setup_component(hass, "repairs", {})
    manager = hass.data["repairs"]["flow_manager"]
    result = await manager.async_init(DOMAIN, data={"issue_id": issue_id})
    return manager, result


# ---- adding: offer the account after local pairing -----------------------


@pytest.mark.usefixtures("unpaired_controller")
@pytest.mark.parametrize("add", [True, False])
async def test_setup_offers_account_after_sign_in(
    hass: HomeAssistant, cloud: FakeCloud, add: bool
) -> None:
    """Paired locally, then signed in: offered to put it on the account."""
    cloud.remove_from_account()
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "pick"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"device": HKID.lower()}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"pairing_code": PAIRING_CODE}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "cloud_login"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_EMAIL: EMAIL, CONF_PASSWORD: PASSWORD}
    )
    assert result["step_id"] == "account"
    assert result["errors"] == {}
    with patch("custom_components.mysa_local.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"add_to_account": add}
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    if not add:
        assert cloud.claims == []
        return
    (claim,) = cloud.claims
    # The smaller claim: a thermostat that was on the account before keeps its
    # heater type, display units and other settings.
    assert claim.pop("LastPaired") > 0
    assert claim == {
        "Id": DEVICE_ID,
        "Name": "Mysa-d4e5f6",
        "Model": "BB-V2-0-L",
        "Home": HOME_ID,
        "TimeZone": str(hass.config.time_zone),
        "schedGlobalOffset": 0,
    }
    assert cloud.on_account


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


# ---- adding: offer local pairing after picking from the account ----------


@pytest.mark.usefixtures("unpaired_controller")
async def test_setup_from_account_then_pair_locally(
    hass: HomeAssistant, cloud: FakeCloud
) -> None:
    """Picked from the account, then paired with the code as well."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "cloud_login"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_EMAIL: EMAIL, CONF_PASSWORD: PASSWORD}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"devices": [DEVICE_ID]}
    )
    assert result["step_id"] == "local_offer"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "local_find"}
    )
    assert result["step_id"] == "pair"
    with patch("custom_components.mysa_local.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"pairing_code": PAIRING_CODE}
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    (subentry,) = result["result"].subentries.values()
    assert subentry.title == "Living Room"
    assert subentry.data[CONF_PAIRING]["AccessoryPairingID"] == HKID


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


# ---- thermostat settings: status, account, local -------------------------


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
    await hass.async_block_till_done()
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
    await hass.async_block_till_done()
    assert hass.states.get("sensor.living_room_mysa_account").state == "on_account"
    assert ir.async_get(hass).async_get_issue(DOMAIN, ACCOUNT_ISSUE) is None


@pytest.mark.usefixtures("unpaired_controller")
async def test_settings_pair_cloud_only_locally(
    hass: HomeAssistant, cloud: FakeCloud
) -> None:
    """A cloud-only thermostat gets paired from its settings."""
    entry = hub_entry(subentries=[cloud_only_subentry()])
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
    await hass.async_block_till_done()
    assert entry.subentries[subentry.subentry_id].data[CONF_PAIRING]


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
        "custom_components.mysa_local.config_flow.async_unpair", AsyncMock()
    ) as unpair:
        result = await hass.config_entries.subentries.async_configure(
            result["flow_id"], {"pairing_code": PAIRING_CODE}
        )
    assert result["reason"] == "wrong_thermostat"
    unpair.assert_awaited_once()
    assert CONF_PAIRING not in entry.subentries[subentry.subentry_id].data


# ---- integration options: the account ------------------------------------


@pytest.mark.usefixtures("controller")
async def test_options_account_page_is_view_only(
    hass: HomeAssistant, cloud: FakeCloud
) -> None:
    """A table of the account's thermostats; nothing can be removed here."""
    entry = hub_entry()
    await setup_entry(hass, entry)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "account"}
    )
    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "account"
    assert result["menu_options"] == ["init"]  # only "Back"
    placeholders = result["description_placeholders"]
    assert (
        "| Living Room | Living Room | Mysa Baseboard Lite | ✅ Local and cloud |"
        in placeholders["thermostats"]
    )
    assert placeholders["add_hint"] == ""  # everything is added already
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "init"}
    )
    assert result["step_id"] == "init"
    assert result["description_placeholders"]["account"] == (
        f"Signed in to Mysa as **{EMAIL.replace('@', '&#64;')}**.\n\n"
        "☐ **Password not saved.** When the Mysa sign-in expires (it seems to "
        "last about a month), Home Assistant asks you to sign in again. To "
        "change this, sign out and sign in again."
    )
    assert cloud.deleted == []


@pytest.mark.usefixtures("controller")
async def test_options_account_page_not_added(
    hass: HomeAssistant, cloud: FakeCloud
) -> None:
    """Thermostats not in Home Assistant get the hint to add them."""
    entry = hub_entry(subentries=[])
    await setup_entry(hass, entry)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "account"}
    )
    placeholders = result["description_placeholders"]
    assert (
        "| — | Living Room | Mysa Baseboard Lite | ⚪ Not added |"
        in (placeholders["thermostats"])
    )
    assert "Add thermostat" in placeholders["add_hint"]


@pytest.mark.usefixtures("controller")
async def test_options_account_page_unsupported(
    hass: HomeAssistant, cloud: FakeCloud
) -> None:
    """An unsupported model is marked as such, with no hint to add it."""
    cloud.add_unsupported()
    entry = hub_entry()
    await setup_entry(hass, entry)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "account"}
    )
    placeholders = result["description_placeholders"]
    assert (
        "| — | Hallway | Mysa Baseboard V3 | ❌ Not supported |"
        in placeholders["thermostats"]
    )
    assert placeholders["add_hint"] == ""


@pytest.mark.usefixtures("controller")
async def test_add_from_account_skips_unsupported(
    hass: HomeAssistant, cloud: FakeCloud
) -> None:
    """With only an unsupported model left on the account, there's nothing to add."""
    cloud.add_unsupported()
    entry = hub_entry()
    await setup_entry(hass, entry)
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "thermostat"), context={"source": config_entries.SOURCE_USER}
    )
    assert "cloud_pick" not in result["menu_options"]


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
    await hass.async_block_till_done()
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
    await hass.async_block_till_done()
    assert entry.subentries[subentry.subentry_id].title == "Living Room"
    device = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, DEVICE_ID), entry.entry_id
    )
    assert device is not None
    assert device.name == "Living Room"
    assert device.model == f"Mysa Baseboard Lite · Mysa ID {DEVICE_ID}"


async def test_options_account_hidden_when_signed_out(hass: HomeAssistant) -> None:
    """No account page without a sign-in."""
    entry = hub_entry(signed_in=False, subentries=[cloud_only_subentry()])
    await setup_entry(hass, entry)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["menu_options"] == ["settings", "sign_in"]


# ---- warnings (repair issues) and their fixes ----------------------------


@pytest.mark.usefixtures("controller")
async def test_paired_not_on_account_issue_and_fix(
    hass: HomeAssistant, cloud: FakeCloud
) -> None:
    """Paired but not on the account: a fixable warning that adds it."""
    cloud.remove_from_account()
    await setup_entry(hass, hub_entry())
    issue = ir.async_get(hass).async_get_issue(DOMAIN, ACCOUNT_ISSUE)
    assert issue is not None
    assert issue.is_fixable
    assert issue.severity is ir.IssueSeverity.WARNING
    assert ir.async_get(hass).async_get_issue(DOMAIN, LOCAL_ISSUE) is None

    manager, result = await fix_flow(hass, ACCOUNT_ISSUE)
    assert result["step_id"] == "account"
    result = await manager.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert cloud.on_account
    await hass.async_block_till_done()
    assert ir.async_get(hass).async_get_issue(DOMAIN, ACCOUNT_ISSUE) is None


@pytest.mark.usefixtures("unpaired_controller")
async def test_cloud_only_issue_and_fix(hass: HomeAssistant, cloud: FakeCloud) -> None:
    """Cloud only: a fixable warning that pairs it locally."""
    entry = hub_entry(subentries=[cloud_only_subentry()])
    await setup_entry(hass, entry)
    issue = ir.async_get(hass).async_get_issue(DOMAIN, LOCAL_ISSUE)
    assert issue is not None
    assert issue.translation_key == "cloud_only"
    assert ir.async_get(hass).async_get_issue(DOMAIN, ACCOUNT_ISSUE) is None

    manager, result = await fix_flow(hass, LOCAL_ISSUE)
    assert result["step_id"] == "pair"
    result = await manager.async_configure(
        result["flow_id"], {"pairing_code": PAIRING_CODE}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    (subentry,) = entry.subentries.values()
    assert subentry.data[CONF_PAIRING]["AccessoryPairingID"] == HKID
    assert ir.async_get(hass).async_get_issue(DOMAIN, LOCAL_ISSUE) is None


@pytest.mark.usefixtures("controller")
async def test_cloud_only_removed_from_account_issue(
    hass: HomeAssistant, cloud: FakeCloud
) -> None:
    """Cloud only and gone from the account: one warning, fix adds it back."""
    cloud.remove_from_account()
    await setup_entry(hass, hub_entry(subentries=[cloud_only_subentry()]))
    issue = ir.async_get(hass).async_get_issue(DOMAIN, ACCOUNT_ISSUE)
    assert issue is not None
    assert issue.translation_key == "removed_from_account"
    assert ir.async_get(hass).async_get_issue(DOMAIN, LOCAL_ISSUE) is None
    manager, result = await fix_flow(hass, ACCOUNT_ISSUE)
    result = await manager.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert cloud.on_account


@pytest.mark.usefixtures("controller")
async def test_no_account_issues_without_cloud(
    hass: HomeAssistant, cloud: FakeCloud
) -> None:
    """Signed out: the account status is unknown, so no warnings."""
    cloud.remove_from_account()
    await setup_entry(hass, hub_entry(signed_in=False))
    assert ir.async_get(hass).async_get_issue(DOMAIN, ACCOUNT_ISSUE) is None
    assert hass.states.get("sensor.living_room_mysa_account") is None
    assert hass.states.get("sensor.living_room_homekit_pairing").state == "paired"


# ---- error paths --------------------------------------------------------


@pytest.mark.usefixtures("unpaired_controller")
async def test_discovered_thermostat_offered_the_account(
    hass: HomeAssistant, cloud: FakeCloud
) -> None:
    """A Mysa discovered after setup is offered the account too."""
    cloud.remove_from_account()
    entry = hub_entry(subentries=[])
    await setup_entry(hass, entry)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_HOMEKIT}, data=discovery()
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"pairing_code": PAIRING_CODE}
    )
    assert result["step_id"] == "account"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"add_to_account": True}
    )
    assert result["reason"] == "thermostat_added"
    assert cloud.on_account
    assert len(entry.subentries) == 1


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


@pytest.mark.usefixtures("controller")
async def test_unfixable_account_issue_has_text_only(
    hass: HomeAssistant, cloud: FakeCloud
) -> None:
    """A model Home Assistant can't add gets the text-only warning."""
    cloud.remove_from_account()
    sub = cloud_only_subentry()
    sub["data"]["model"] = "BB-V3-0"
    await setup_entry(hass, hub_entry(subentries=[sub]))
    issue = ir.async_get(hass).async_get_issue(DOMAIN, ACCOUNT_ISSUE)
    assert issue is not None
    assert not issue.is_fixable
    assert issue.translation_key == "removed_from_account_info"
