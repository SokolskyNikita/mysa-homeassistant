"""Remembered HomeKit code consent, persistence, and independent deletion."""

from unittest.mock import AsyncMock, patch

from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.storage import Store
import pytest

from custom_components.mysa_local.const import CONF_PAIRING, DOMAIN
from custom_components.mysa_local.diagnostics import async_get_config_entry_diagnostics
from custom_components.mysa_local.homekit_adapter import async_unpair
from custom_components.mysa_local.homekit_codes import (
    CONF_REMEMBER_HOMEKIT_CODE,
    STORAGE_KEY,
    HomeKitCodes,
    homekit_codes,
)

from .common import HKID, PAIRING_CODE, PAIRING_DATA, discovery, hub_entry
from .conftest import setup_entry


@pytest.mark.usefixtures("unpaired_controller")
@pytest.mark.parametrize("remember", [True, False])
async def test_pairing_remembers_only_successful_opted_in_code(hass, remember, caplog):
    """Failed attempts never persist codes; opt-in survives a store reload."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_HOMEKIT}, data=discovery()
    )
    schema = result["data_schema"].schema
    checkbox = next(k for k in schema if k == CONF_REMEMBER_HOMEKIT_CODE)
    assert checkbox.default() is False
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"pairing_code": "999-99-998", CONF_REMEMBER_HOMEKIT_CODE: remember},
    )
    assert result["errors"] == {"pairing_code": "wrong_code"}
    assert await homekit_codes(hass).async_count() == 0
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"pairing_code": PAIRING_CODE, CONF_REMEMBER_HOMEKIT_CODE: remember},
    )
    assert result["step_id"] == "cloud_offer"
    assert await HomeKitCodes(hass).async_count() == int(remember)
    saved = await Store(hass, 1, STORAGE_KEY, private=True).async_load()
    assert saved == ({HKID.lower(): PAIRING_CODE} if remember else None)
    assert all(
        PAIRING_CODE not in record.getMessage()
        for record in caplog.records
        if not record.name.startswith("pytest_homeassistant_custom_component")
    )


async def test_forget_menu_deletes_all_codes_but_preserves_pairing(hass, controller):
    """Opening the confirmation is harmless; submitting forgets codes only."""
    entry = hub_entry(signed_in=False, cloud_enabled=False)
    await setup_entry(hass, entry)
    before = {s.subentry_id: dict(s.data) for s in entry.subentries.values()}
    codes = homekit_codes(hass)
    await codes.async_save(HKID, PAIRING_CODE)
    await codes.async_save("another-device", "999-99-998")
    diagnostics = await async_get_config_entry_diagnostics(hass, entry)
    assert PAIRING_CODE not in str(diagnostics)
    assert "999-99-998" not in str(diagnostics)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert "forget_homekit_codes" in result["menu_options"]
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "forget_homekit_codes"}
    )
    assert result["description_placeholders"] == {"count": "2"}
    assert await codes.async_count() == 2
    result = await hass.config_entries.options.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert await HomeKitCodes(hass).async_count() == 0
    assert {s.subentry_id: dict(s.data) for s in entry.subentries.values()} == before
    assert all(s.data[CONF_PAIRING] for s in entry.subentries.values())
    assert next(iter(entry.runtime_data.thermostats.values())).local.available
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert "forget_homekit_codes" not in result["menu_options"]


async def test_unpair_forgets_only_that_accessory_code(hass):
    """Device cleanup removes its code without affecting another thermostat."""
    codes = homekit_codes(hass)
    await codes.async_save(HKID, PAIRING_CODE)
    await codes.async_save("another-device", "999-99-998")
    with patch(
        "custom_components.mysa_local.homekit_adapter.async_get_controller",
        AsyncMock(return_value=AsyncMock()),
    ):
        await async_unpair(hass, PAIRING_DATA)
    assert await Store(hass, 1, STORAGE_KEY, private=True).async_load() == {
        "another-device": "999-99-998"
    }
