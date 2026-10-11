"""Integration options and account lifecycle flows."""

from __future__ import annotations

from datetime import timedelta

from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD, CONF_SCAN_INTERVAL
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.mysa_local.const import (
    CONF_CLOUD_ENABLED,
    CONF_FIRMWARE_INSTALL,
    CONF_LIVE_MODE,
    CONF_REFRESH_TOKEN,
    CONF_SAVE_PASSWORD,
    DOMAIN,
)

from .common import (
    DEVICE_ID,
    EMAIL,
    PASSWORD,
    REFRESH_TOKEN,
    FakeCloud,
    cloud_only_subentry,
    hub_entry,
)
from .conftest import setup_entry


@pytest.mark.usefixtures("controller")
async def test_reauth(hass: HomeAssistant, cloud: FakeCloud) -> None:
    """An expired sign-in asks for the password again."""
    cloud.refresh_ok = False
    entry = hub_entry()
    await setup_entry(hass, entry)
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [f["context"]["source"] for f in flows] == ["reauth"]
    # Local control keeps working meanwhile.
    assert hass.states.get("climate.living_room").state == "heat"

    cloud.refresh_ok = True
    result = await hass.config_entries.flow.async_configure(
        flows[0]["flow_id"], {CONF_EMAIL: EMAIL, CONF_PASSWORD: PASSWORD}
    )
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_REFRESH_TOKEN] == REFRESH_TOKEN


@pytest.mark.usefixtures("controller")
async def test_saved_password_renews_expired_sign_in(
    hass: HomeAssistant, cloud: FakeCloud
) -> None:
    """With the password saved, an expired refresh token is replaced silently."""
    cloud.refresh_ok = False
    entry = hub_entry(password=PASSWORD, refresh_token="expired-token")
    await setup_entry(hass, entry)
    assert hass.config_entries.flow.async_progress_by_handler(DOMAIN) == []
    assert entry.data[CONF_REFRESH_TOKEN] == REFRESH_TOKEN  # from the new sign-in
    assert entry.data[CONF_PASSWORD] == PASSWORD
    assert hass.states.get("switch.living_room_button_lock").state == "off"


@pytest.mark.usefixtures("controller")
async def test_saved_password_that_stopped_working(
    hass: HomeAssistant, cloud: FakeCloud
) -> None:
    """A changed password still ends in reauth, which saves the new one."""
    cloud.refresh_ok = False
    cloud.password = "changed-in-the-app"
    entry = hub_entry(password=PASSWORD, refresh_token="expired-token")
    await setup_entry(hass, entry)
    (flow,) = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert flow["context"]["source"] == "reauth"

    result = await hass.config_entries.flow.async_configure(
        flow["flow_id"], {CONF_EMAIL: EMAIL, CONF_PASSWORD: "changed-in-the-app"}
    )
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_PASSWORD] == "changed-in-the-app"
    assert entry.data[CONF_REFRESH_TOKEN] == REFRESH_TOKEN


@pytest.mark.usefixtures("controller")
async def test_reauth_keeps_password_unsaved(
    hass: HomeAssistant, cloud: FakeCloud
) -> None:
    """Without a saved password, reauth doesn't start saving one."""
    cloud.refresh_ok = False
    entry = hub_entry()
    await setup_entry(hass, entry)
    (flow,) = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    cloud.refresh_ok = True
    await hass.config_entries.flow.async_configure(
        flow["flow_id"], {CONF_EMAIL: EMAIL, CONF_PASSWORD: PASSWORD}
    )
    assert CONF_PASSWORD not in entry.data


@pytest.mark.usefixtures("controller")
@pytest.mark.parametrize("path", [f"/capabilities/{DEVICE_ID}", "/state/batch"])
async def test_refused_request_keeps_sign_in(
    hass: HomeAssistant, cloud: FakeCloud, path: str
) -> None:
    """A 403 from one of Mysa's routes doesn't ask for the password again."""
    cloud.path_status[path] = 403
    await setup_entry(hass, hub_entry())
    assert not hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert hass.states.get("climate.living_room").state == "heat"


@pytest.mark.usefixtures("controller")
async def test_options_cloud_off_hides_cloud_entities(
    hass: HomeAssistant, cloud: FakeCloud, freezer: FrozenDateTimeFactory
) -> None:
    """Cloud off: no cloud calls, cloud-only entities disabled with their settings.

    Turning the cloud back on re-enables exactly those, not one the user
    disabled.
    """
    entry = hub_entry()
    await setup_entry(hass, entry)
    assert hass.states.get("switch.living_room_button_lock") is not None
    ent_reg = er.async_get(hass)
    ent_reg.async_update_entity("switch.living_room_button_lock", name="Kid lock")
    ent_reg.async_update_entity(
        "number.living_room_wake_brightness",
        disabled_by=er.RegistryEntryDisabler.USER,
    )

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.MENU
    assert result["menu_options"] == ["settings", "account", "sign_out"]
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "settings"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_CLOUD_ENABLED: False,
            CONF_SCAN_INTERVAL: 120,
            CONF_LIVE_MODE: False,
            "advanced": {CONF_FIRMWARE_INSTALL: False},
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.options[CONF_CLOUD_ENABLED] is False
    assert entry.state is ConfigEntryState.LOADED

    lock = ent_reg.async_get("switch.living_room_button_lock")
    assert lock is not None
    assert lock.disabled_by is er.RegistryEntryDisabler.INTEGRATION
    assert lock.name == "Kid lock"
    assert hass.states.get("switch.living_room_button_lock") is None
    assert entry.data[CONF_REFRESH_TOKEN] == REFRESH_TOKEN  # sign-in kept

    calls = cloud.mock.call_count
    async_fire = hass.config_entries.async_entries(DOMAIN)[0].runtime_data
    assert async_fire.cloud is None
    await hass.async_block_till_done()
    assert cloud.mock.call_count == calls

    hass.config_entries.async_update_entry(
        entry, options={**entry.options, CONF_CLOUD_ENABLED: True}
    )
    await hass.async_block_till_done()
    freezer.tick(timedelta(seconds=31))  # Home Assistant's reload after re-enabling
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    lock = ent_reg.async_get("switch.living_room_button_lock")
    assert lock is not None
    assert lock.disabled_by is None
    assert lock.name == "Kid lock"
    assert lock.options.get(DOMAIN) is None
    assert hass.states.get("switch.living_room_button_lock") is not None
    wake = ent_reg.async_get("number.living_room_wake_brightness")
    assert wake is not None
    assert wake.disabled_by is er.RegistryEntryDisabler.USER
    wifi = ent_reg.async_get("sensor.living_room_wi_fi_signal")
    assert wifi is not None
    assert wifi.disabled_by is er.RegistryEntryDisabler.INTEGRATION  # still default


@pytest.mark.usefixtures("controller", "cloud")
async def test_options_live_mode(hass: HomeAssistant) -> None:
    """Live mode is off by default and is switched on in the settings form."""
    entry = hub_entry()
    await setup_entry(hass, entry)
    assert entry.runtime_data.cloud is not None
    assert entry.runtime_data.cloud.live_mode is False

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "settings"}
    )
    schema = result["data_schema"].schema
    field = next(key for key in schema if key == CONF_LIVE_MODE)
    assert field.description == {"suggested_value": False}
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_CLOUD_ENABLED: True,
            CONF_SCAN_INTERVAL: 60,
            CONF_LIVE_MODE: True,
            "advanced": {CONF_FIRMWARE_INSTALL: False},
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.options[CONF_LIVE_MODE] is True
    assert entry.state is ConfigEntryState.LOADED
    assert entry.runtime_data.cloud is not None
    assert entry.runtime_data.cloud.live_mode is True


@pytest.mark.usefixtures("controller")
async def test_options_live_mode_needs_sign_in(hass: HomeAssistant) -> None:
    """Without a Mysa sign-in, the settings form leaves out the cloud switches."""
    entry = hub_entry(signed_in=False)
    await setup_entry(hass, entry)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "settings"}
    )
    schema = result["data_schema"].schema
    assert CONF_LIVE_MODE not in schema
    assert CONF_CLOUD_ENABLED not in schema


@pytest.mark.usefixtures("controller")
async def test_options_sign_out_and_in(hass: HomeAssistant, cloud: FakeCloud) -> None:
    """Sign out deletes the token; sign in brings it back."""
    entry = hub_entry()
    await setup_entry(hass, entry)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "sign_out"}
    )
    result = await hass.config_entries.options.async_configure(result["flow_id"], {})
    await hass.async_block_till_done()
    assert CONF_REFRESH_TOKEN not in entry.data

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["menu_options"] == ["settings", "sign_in"]
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "sign_in"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_EMAIL: EMAIL, CONF_PASSWORD: PASSWORD, CONF_SAVE_PASSWORD: True},
    )
    await hass.async_block_till_done()
    assert entry.data[CONF_REFRESH_TOKEN] == REFRESH_TOKEN
    assert entry.data[CONF_PASSWORD] == PASSWORD
    assert hass.states.get("switch.living_room_button_lock") is not None

    # The options page shows the choice; signing out forgets the password too.
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert "☑ **Password saved.**" in result["description_placeholders"]["account"]
    assert "sign out and sign in again" in result["description_placeholders"]["account"]
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "sign_out"}
    )
    await hass.config_entries.options.async_configure(result["flow_id"], {})
    await hass.async_block_till_done()
    assert CONF_PASSWORD not in entry.data


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


async def test_options_account_hidden_when_signed_out(hass: HomeAssistant) -> None:
    """No account page without a sign-in."""
    entry = hub_entry(signed_in=False, subentries=[cloud_only_subentry()])
    await setup_entry(hass, entry)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["menu_options"] == ["settings", "sign_in"]
