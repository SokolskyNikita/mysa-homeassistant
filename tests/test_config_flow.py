"""Initial Mysa setup and discovery flows."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

from homeassistant import config_entries
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
import pytest

from custom_components.mysa_local.const import (
    CONF_DEVICE_ID,
    CONF_PAIRING,
    CONF_REFRESH_TOKEN,
    CONF_SAVE_PASSWORD,
    CONF_USER_ID,
    DOMAIN,
    HOMEKIT_MODEL_PATTERNS,
    SUBENTRY_THERMOSTAT,
)

from .common import (
    DEVICE_ID,
    EMAIL,
    HKID,
    HOME_ID,
    PAIRING_CODE,
    PASSWORD,
    REFRESH_TOKEN,
    USER_ID,
    FakeCloud,
    discovery,
    hub_entry,
)
from .conftest import setup_entry


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


@pytest.mark.parametrize("model", ["BB-V3-0", "ST-V1-0", "CS-V1-0"])
async def test_discovery_of_unsupported_model(hass: HomeAssistant, model: str) -> None:
    """Newer models remain excluded from HomeKit discovery."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_HOMEKIT},
        data=discovery(model=model),
    )
    assert result["reason"] == "not_supported"


def test_manifest_homekit_models_match() -> None:
    """The manifest claims exactly the HomeKit models the flows accept."""
    manifest = Path(__file__).parents[1] / "custom_components/mysa_local/manifest.json"
    models = json.loads(manifest.read_text())["homekit"]["models"]
    assert tuple(models) == HOMEKIT_MODEL_PATTERNS


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
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"devices": [DEVICE_ID]}
    )
    # Offered local pairing too; skip it.
    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "local_offer"
    with patch("custom_components.mysa_local.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "local_skip"}
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    (subentry,) = result["result"].subentries.values()
    assert CONF_PAIRING not in subentry.data
    assert subentry.title == "Living Room"


async def test_cloud_only_setup_includes_new_models(
    hass: HomeAssistant, cloud: FakeCloud
) -> None:
    """Enrollment restrictions do not exclude already enrolled cloud models."""
    added = cloud.add_unsupported()
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
    (key,) = result["data_schema"].schema
    assert key.default() == [DEVICE_ID, added]


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
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"devices": [DEVICE_ID]}
    )
    with patch("custom_components.mysa_local.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "local_skip"}
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
    from custom_components.mysa_local.protocols.cloud import (
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


async def test_sign_in_saves_password_when_asked(
    hass: HomeAssistant, cloud: FakeCloud
) -> None:
    """Ticking "Save my password" stores it with the sign-in; it's off by default."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "cloud_login"}
    )
    assert (
        result["data_schema"]({CONF_EMAIL: EMAIL, CONF_PASSWORD: PASSWORD})[
            CONF_SAVE_PASSWORD
        ]
        is False
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_EMAIL: EMAIL, CONF_PASSWORD: PASSWORD, CONF_SAVE_PASSWORD: True},
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"devices": [DEVICE_ID]}
    )
    with patch("custom_components.mysa_local.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "local_skip"}
        )
    assert result["data"] == {
        CONF_EMAIL: EMAIL,
        CONF_PASSWORD: PASSWORD,
        CONF_REFRESH_TOKEN: REFRESH_TOKEN,
        CONF_USER_ID: USER_ID,
    }


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

    await hass.async_block_till_done(wait_background_tasks=True)
