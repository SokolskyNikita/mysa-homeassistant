"""Account status repair issues and their fixes."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import issue_registry as ir
from homeassistant.setup import async_setup_component
import pytest

from custom_components.mysa_local.const import CONF_PAIRING, DOMAIN

from .common import (
    ACCOUNT_ISSUE,
    HKID,
    LOCAL_ISSUE,
    PAIRING_CODE,
    FakeCloud,
    cloud_only_subentry,
    hub_entry,
)
from .conftest import setup_entry


async def fix_flow(hass: HomeAssistant, issue_id: str) -> tuple[Any, dict[str, Any]]:
    """Start the repair flow of an issue."""
    assert await async_setup_component(hass, "repairs", {})
    manager = hass.data["repairs"]["flow_manager"]
    result = await manager.async_init(DOMAIN, data={"issue_id": issue_id})
    return manager, result


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
