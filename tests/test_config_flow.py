"""Config, subentry, reauth and options flows."""

from __future__ import annotations

from ipaddress import ip_address
from unittest.mock import AsyncMock, patch

from aiohomekit.exceptions import MalformedPinError
from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD, CONF_SCAN_INTERVAL
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mysa_local.config_flow import InsecureCodeError, format_pin
from custom_components.mysa_local.const import (
    CONF_CLOUD_ENABLED,
    CONF_CLOUD_RUNTIME,
    CONF_DEVICE_ID,
    CONF_FIRMWARE_INSTALL,
    CONF_HEATER_WATTS,
    CONF_MIGRATE_FROM,
    CONF_PAIRING,
    CONF_REFRESH_TOKEN,
    CONF_USER_ID,
    DOMAIN,
    SUBENTRY_THERMOSTAT,
)
from custom_components.mysa_local.local import AccessoryInfo

from .common import (
    DEVICE_ID,
    EMAIL,
    HKID,
    PAIRING_CODE,
    PAIRING_DATA,
    PASSWORD,
    REFRESH_TOKEN,
    USER_ID,
    FakeCloud,
    hub_entry,
)
from .conftest import setup_entry


def discovery(*, paired: bool = False, host: str = "192.0.2.10") -> ZeroconfServiceInfo:
    """HomeKit advertisement of the Lite."""
    return ZeroconfServiceInfo(
        ip_address=ip_address(host),
        ip_addresses=[ip_address(host)],
        hostname="Mysa-d4e5f6.local.",
        name="Mysa-d4e5f6._hap._tcp.local.",
        port=57959,
        type="_hap._tcp.local.",
        properties={
            "md": "BB-V2-0-L",
            "id": HKID,
            "sf": "0" if paired else "1",
            "c#": "1",
            "ci": "9",
        },
    )


@pytest.mark.usefixtures("unpaired_controller")
async def test_user_pick_and_pair_local_only(hass: HomeAssistant) -> None:
    """Pick a discovered Lite, enter the code, skip the cloud."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.MENU
    assert result["menu_options"] == ["pick", "cloud_login", "wifi_help"]

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "pick"}
    )
    assert result["step_id"] == "pick"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"device": HKID.lower()}
    )
    assert result["step_id"] == "pair"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"pairing_code": "999-99-998"}
    )
    assert result["errors"] == {"pairing_code": "wrong_code"}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"pairing_code": "12345678"}
    )
    assert result["errors"] == {"pairing_code": "insecure_code"}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"pairing_code": "111 22"}
    )
    assert result["errors"] == {"pairing_code": "invalid_code"}

    # Typed with spaces, as people copy it off the faceplate.
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"pairing_code": " 1112 2333 "}
    )
    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "cloud_offer"

    with patch("custom_components.mysa_local.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "finish"}
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {}
    entry = result["result"]
    assert entry.unique_id == DOMAIN
    (subentry,) = entry.subentries.values()
    assert subentry.subentry_type == SUBENTRY_THERMOSTAT
    assert subentry.unique_id == DEVICE_ID
    assert subentry.title == "Mysa-d4e5f6"
    assert subentry.data[CONF_DEVICE_ID] == DEVICE_ID
    assert subentry.data[CONF_PAIRING]["AccessoryPairingID"] == HKID


@pytest.mark.usefixtures("unpaired_controller")
async def test_discovery_pair_and_sign_in(
    hass: HomeAssistant, cloud: FakeCloud
) -> None:
    """Discovered Lite + Mysa sign-in (real SRP against the fake Cognito)."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_HOMEKIT}, data=discovery()
    )
    assert result["step_id"] == "pair"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"pairing_code": PAIRING_CODE}
    )
    assert result["step_id"] == "cloud_offer"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "cloud_login"}
    )
    assert result["step_id"] == "cloud_login"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_EMAIL: EMAIL, CONF_PASSWORD: "wrong"}
    )
    assert result["errors"] == {"base": "invalid_auth"}

    with patch("custom_components.mysa_local.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_EMAIL: EMAIL, CONF_PASSWORD: PASSWORD}
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {
        CONF_EMAIL: EMAIL,
        CONF_REFRESH_TOKEN: REFRESH_TOKEN,
        CONF_USER_ID: USER_ID,
    }
    (subentry,) = result["result"].subentries.values()
    # Named after the thermostat's Mysa name.
    assert subentry.title == "Living Room"


@pytest.mark.usefixtures("unpaired_controller")
async def test_discovery_adds_to_existing_entry(hass: HomeAssistant) -> None:
    """A second thermostat joins the existing entry as a subentry."""
    entry = hub_entry(signed_in=False, subentries=[])
    entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_HOMEKIT}, data=discovery()
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"pairing_code": PAIRING_CODE}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "thermostat_added"
    (subentry,) = entry.subentries.values()
    assert subentry.unique_id == DEVICE_ID


async def test_discovery_of_configured_updates_address(hass: HomeAssistant) -> None:
    """A known thermostat that moved gets its new address stored."""
    entry = hub_entry(signed_in=False)
    entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_HOMEKIT},
        data=discovery(paired=True, host="192.0.2.99"),
    )
    assert result["reason"] == "already_configured"
    (subentry,) = entry.subentries.values()
    assert subentry.data[CONF_PAIRING]["AccessoryIP"] == "192.0.2.99"


async def test_discovery_paired_elsewhere(hass: HomeAssistant) -> None:
    """Paired to Apple Home (or similar): nothing we can do."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_HOMEKIT},
        data=discovery(paired=True),
    )
    assert result["reason"] == "already_paired"


async def test_user_when_configured(hass: HomeAssistant) -> None:
    """Only one entry; more thermostats are subentries."""
    hub_entry(signed_in=False).add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["reason"] == "already_configured_hub"


async def test_cloud_only_setup(hass: HomeAssistant, cloud: FakeCloud) -> None:
    """Sign in first and add thermostats from the account."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "cloud_login"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_EMAIL: EMAIL, CONF_PASSWORD: PASSWORD}
    )
    assert result["step_id"] == "cloud_devices"
    with patch("custom_components.mysa_local.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"devices": [DEVICE_ID]}
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    (subentry,) = result["result"].subentries.values()
    assert CONF_PAIRING not in subentry.data
    assert subentry.title == "Living Room"


async def test_sign_in_survives_bootstrap_failure(
    hass: HomeAssistant, cloud: FakeCloud
) -> None:
    """If /mobile/bootstrap fails, sign-in still works from the device list."""
    cloud.bootstrap_status = 400
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "cloud_login"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_EMAIL: EMAIL, CONF_PASSWORD: PASSWORD}
    )
    assert result["step_id"] == "cloud_devices"
    with patch("custom_components.mysa_local.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"devices": [DEVICE_ID]}
        )
    assert result["data"][CONF_USER_ID] == USER_ID  # from the token's claims


@pytest.mark.parametrize(
    ("error", "key"),
    [
        ("connection", "cannot_connect"),
        ("challenge", "mfa_unsupported"),
    ],
)
async def test_sign_in_errors(hass: HomeAssistant, error: str, key: str) -> None:
    """Sign-in problems show as form errors."""
    from custom_components.mysa_local.api import (
        MysaConnectionError,
        MysaUnsupportedChallengeError,
    )

    exc = (
        MysaConnectionError("down")
        if error == "connection"
        else MysaUnsupportedChallengeError("SMS_MFA")
    )
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "cloud_login"}
    )
    with patch(
        "custom_components.mysa_local.config_flow.async_sign_in",
        AsyncMock(side_effect=exc),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_EMAIL: EMAIL, CONF_PASSWORD: PASSWORD}
        )
    assert result["errors"] == {"base": key}


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
async def test_options_cloud_off_removes_cloud_entities(
    hass: HomeAssistant, cloud: FakeCloud
) -> None:
    """Cloud off: no cloud calls and no cloud-only entities."""
    entry = hub_entry()
    await setup_entry(hass, entry)
    assert hass.states.get("switch.living_room_button_lock") is not None

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.MENU
    assert result["menu_options"] == ["settings", "sign_out"]
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "settings"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_CLOUD_ENABLED: False,
            CONF_SCAN_INTERVAL: 120,
            "advanced": {CONF_FIRMWARE_INSTALL: False},
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.options[CONF_CLOUD_ENABLED] is False
    assert entry.state is ConfigEntryState.LOADED

    ent_reg = er.async_get(hass)
    assert ent_reg.async_get("switch.living_room_button_lock") is None
    assert entry.data[CONF_REFRESH_TOKEN] == REFRESH_TOKEN  # sign-in kept

    calls = cloud.mock.call_count
    async_fire = hass.config_entries.async_entries(DOMAIN)[0].runtime_data
    assert async_fire.cloud is None
    await hass.async_block_till_done()
    assert cloud.mock.call_count == calls


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
        result["flow_id"], {CONF_EMAIL: EMAIL, CONF_PASSWORD: PASSWORD}
    )
    await hass.async_block_till_done()
    assert entry.data[CONF_REFRESH_TOKEN] == REFRESH_TOKEN
    assert hass.states.get("switch.living_room_button_lock") is not None


@pytest.mark.usefixtures("controller")
async def test_subentry_reconfigure_wattage(hass: HomeAssistant) -> None:
    """Setting a wattage adds the energy sensor."""
    entry = hub_entry(signed_in=False)
    await setup_entry(hass, entry)
    assert hass.states.get("sensor.living_room_heater_energy_today") is None
    (subentry,) = entry.subentries.values()

    result = await entry.start_subentry_reconfigure_flow(hass, subentry.subentry_id)
    assert result["step_id"] == "reconfigure"
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {CONF_HEATER_WATTS: 1500, CONF_CLOUD_RUNTIME: False}
    )
    assert result["reason"] == "reconfigure_successful"
    await hass.async_block_till_done()
    assert subentry.data[CONF_HEATER_WATTS] == 1500
    assert hass.states.get("sensor.living_room_heater_energy_today").state == "0.0"


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


async def test_takeover_from_homekit_device(
    hass: HomeAssistant, controller: object
) -> None:
    """Move a Lite from HomeKit Device, keeping its entity IDs."""
    hkc = MockConfigEntry(
        domain="homekit_controller",
        title="Mysa-d4e5f6",
        data=dict(PAIRING_DATA),
        unique_id=HKID.lower(),
    )
    hkc.add_to_hass(hass)
    dev_reg = dr.async_get(hass)
    ent_reg = er.async_get(hass)
    device = dev_reg.async_get_or_create(
        config_entry_id=hkc.entry_id,
        identifiers={("homekit_controller", "x")},
        manufacturer="Empowered Homes Inc.",
        model="BB-V2-0-L",
        name="Mysa-d4e5f6",
    )
    old_climate = ent_reg.async_get_or_create(
        "climate",
        "homekit_controller",
        "x_1_256",
        config_entry=hkc,
        device_id=device.id,
        suggested_object_id="mysa_d4e5f6_thermostat",
    )
    old_temp = ent_reg.async_get_or_create(
        "sensor",
        "homekit_controller",
        "x_1_261",
        config_entry=hkc,
        device_id=device.id,
        original_device_class="temperature",
        suggested_object_id="mysa_d4e5f6_current_temperature",
    )

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert "takeover" in result["menu_options"]
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "takeover"}
    )
    assert result["step_id"] == "takeover_confirm"
    info = AccessoryInfo(name="Mysa-d4e5f6", model="BB-V2-0-L", mac=DEVICE_ID)
    with patch(
        "custom_components.mysa_local.config_flow.async_copy_pairing",
        AsyncMock(return_value=(dict(PAIRING_DATA), info)),
    ):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["step_id"] == "cloud_offer"

    # Stands in for HomeKit Device's own cleanup (it unpairs its controller).
    with patch.object(hkc, "async_remove", AsyncMock()):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "finish"}
        )
        await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    entry = result["result"]
    (subentry,) = entry.subentries.values()
    assert CONF_MIGRATE_FROM not in subentry.data

    # Same entity IDs, now ours; the HomeKit Device entry is gone.
    assert hass.config_entries.async_get_entry(hkc.entry_id) is None
    climate = ent_reg.async_get(old_climate.entity_id)
    assert climate is not None
    assert climate.platform == DOMAIN
    assert climate.unique_id == f"{DEVICE_ID}_climate"
    assert ent_reg.async_get(old_temp.entity_id).unique_id == f"{DEVICE_ID}_temperature"
    assert hass.states.get(old_climate.entity_id).state == "heat"


@pytest.mark.parametrize(
    ("typed", "expected"),
    [
        ("24680135", "246-80-135"),
        ("246-80-135", "246-80-135"),
        ("2468 0135", "246-80-135"),
        (" 246 80 135 ", "246-80-135"),
        ("246.80.135", "246-80-135"),
        ("2468-0135", "246-80-135"),
    ],
)
def test_format_pin_accepts_any_separators(typed: str, expected: str) -> None:
    """Only the digits matter."""
    assert format_pin(typed) == expected


@pytest.mark.parametrize("typed", ["", "1234567", "123456789", "abc-de-fgh"])
def test_format_pin_needs_eight_digits(typed: str) -> None:
    """Anything other than 8 digits is rejected."""
    with pytest.raises(MalformedPinError):
        format_pin(typed)


def test_format_pin_rejects_trivial_codes() -> None:
    """HomeKit never uses codes like 111-11-111."""
    with pytest.raises(InsecureCodeError):
        format_pin("1111 1111")


@pytest.mark.usefixtures("unpaired_controller")
async def test_pairing_retries_after_connection_error(hass: HomeAssistant) -> None:
    """A dozing thermostat doesn't end the flow; the user wakes it and retries."""
    from aiohomekit.exceptions import AccessoryDisconnectedError

    from custom_components.mysa_local import config_flow

    controller = await config_flow.async_get_controller(hass)
    lite = controller.discoveries[HKID]
    real_start = lite.async_start_pairing
    calls = 0

    async def flaky_start(alias: str) -> object:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise AccessoryDisconnectedError("HAP server restarting")
        return await real_start(alias)

    lite.async_start_pairing = flaky_start  # type: ignore[method-assign]
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_HOMEKIT}, data=discovery()
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"pairing_code": PAIRING_CODE}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect_device"}
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"pairing_code": PAIRING_CODE}
    )
    assert result["step_id"] == "cloud_offer"
