"""Shared HomeKit pairing and thermostat flow behavior."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from homeassistant import config_entries
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr, entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mysa_local import async_setup
from custom_components.mysa_local.config_flow import MysaConfigFlow
from custom_components.mysa_local.const import (
    CONF_MIGRATE_FROM,
    DOMAIN,
)
from custom_components.mysa_local.flows.shared import (
    InsecureCodeError,
    configured_hkids,
    format_pin,
)
from custom_components.mysa_local.flows.subentry import ThermostatSubentryFlow
from custom_components.mysa_local.pairing_store import (
    _PAIRING_SESSION,
    _PENDING_PAIRINGS_STORE,
    async_clear_configured_pairing_backups,
    async_load_pending_pairings,
    pairing_session_id,
    pairing_store,
)
from custom_components.mysa_local.protocols.homekit import (
    AccessoryInfo,
    HomeKitConnectionError,
    HomeKitMalformedPinError,
)
from custom_components.mysa_local.protocols.homekit.device import HomeKitDevice
from custom_components.mysa_local.protocols.homekit.models import HomeKitDiscovery
from custom_components.mysa_local.repairs import LocalPairRepairFlow

from .common import (
    DEVICE_ID,
    HKID,
    PAIRING_CODE,
    PAIRING_DATA,
    discovery,
    hub_entry,
    thermostat_subentry,
)


def test_thermostat_flow_state_is_isolated_between_flow_instances() -> None:
    """Setup, subentry, and repair flows each own their pairing state."""
    setup_flow = MysaConfigFlow()
    subentry_flow = ThermostatSubentryFlow()
    repair_flow = LocalPairRepairFlow({"entry_id": "entry", "subentry_id": "subentry"})

    setup_state = setup_flow._thermostat
    subentry_state = subentry_flow._thermostat
    repair_state = repair_flow._thermostat
    assert setup_state is not subentry_state
    assert setup_state is not repair_state
    assert subentry_state is not repair_state
    assert setup_state.hkid is subentry_state.hkid is repair_state.hkid is None

    setup_state.hkid = HKID
    setup_state.pending_recoveries[HKID] = {"pairing": PAIRING_DATA}
    subentry_state.target_device_id = DEVICE_ID
    repair_state.account_optional = True

    assert subentry_state.hkid is None
    assert repair_state.hkid is None
    assert setup_state.target_device_id is None
    assert repair_state.target_device_id is None
    assert setup_state.account_optional is False
    assert subentry_state.account_optional is False
    assert subentry_state.pending_recoveries == {}
    assert repair_state.pending_recoveries == {}


@pytest.mark.usefixtures("unpaired_controller")
async def test_pairing_recovers_after_flow_cancellation_and_store_reload(
    hass: HomeAssistant,
) -> None:
    """A completed HomeKit pairing survives canceling optional cloud setup."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_HOMEKIT}, data=discovery()
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"pairing_code": PAIRING_CODE}
    )
    assert result["step_id"] == "cloud_offer"
    assert (await async_load_pending_pairings(hass))[HKID.lower()][
        "pairing"
    ] == PAIRING_DATA

    hass.config_entries.flow.async_abort(result["flow_id"])
    # A new Store and session ID simulate HA restarting after flow cancellation.
    hass.data.pop(_PENDING_PAIRINGS_STORE)
    old_session = (await async_load_pending_pairings(hass))[HKID.lower()]["session"]
    hass.data.pop(_PAIRING_SESSION)
    assert pairing_session_id(hass) != old_session
    assert (await async_load_pending_pairings(hass))[HKID.lower()][
        "pairing"
    ] == PAIRING_DATA

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_HOMEKIT},
        data=discovery(paired=True),
    )
    assert result["step_id"] == "cloud_offer", result

    # Resume completed without asking for the HomeKit code again.
    hass.config_entries.flow.async_abort(result["flow_id"])
    assert (await async_load_pending_pairings(hass))[HKID.lower()][
        "pairing"
    ] == PAIRING_DATA


@pytest.mark.usefixtures("unpaired_controller")
async def test_pairing_recovers_after_accessory_discovery_failure(
    hass: HomeAssistant,
) -> None:
    """Persist credentials before the post-pair accessory read can fail."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_HOMEKIT}, data=discovery()
    )
    with patch.object(
        HomeKitDevice,
        "async_get_info",
        new_callable=AsyncMock,
        side_effect=HomeKitConnectionError("device restarted"),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"pairing_code": PAIRING_CODE}
        )
    assert result["errors"] == {"base": "cannot_connect_device"}
    assert (await async_load_pending_pairings(hass))[HKID.lower()][
        "pairing"
    ] == PAIRING_DATA

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"pairing_code": PAIRING_CODE}
    )
    assert result["step_id"] == "cloud_offer"

    hass.config_entries.flow.async_abort(result["flow_id"])


@pytest.mark.usefixtures("unpaired_controller")
async def test_pairing_backup_is_pruned_after_startup_loads_entry(
    hass: HomeAssistant,
) -> None:
    """Prune only old backups for pairings in entries loaded from storage."""
    await pairing_store(hass).async_save(
        HKID.lower(),
        PAIRING_DATA,
        name="Living Room",
        model="BB-V2-0-L",
    )
    other_hkid = "11:11:11:11:11:11"
    await pairing_store(hass).async_save(
        other_hkid,
        {**PAIRING_DATA, "AccessoryPairingID": other_hkid},
        name="Other Room",
        model="BB-V2-0-L",
    )
    entry = hub_entry(subentries=[thermostat_subentry()])
    # Represent the entry as it was loaded from HA's already persisted storage.
    entry.add_to_hass(hass)
    assert configured_hkids(entry) == {HKID.lower(): next(iter(entry.subentries))}
    pending = await async_load_pending_pairings(hass)
    old_session = pending[HKID.lower()]["session"]
    await async_clear_configured_pairing_backups(hass, set(configured_hkids(entry)))
    assert set(await async_load_pending_pairings(hass)) == {HKID.lower(), other_hkid}

    hass.data.pop(_PAIRING_SESSION)
    assert pairing_session_id(hass) != old_session

    assert await async_setup(hass, {})
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    await hass.async_block_till_done()

    assert set(await async_load_pending_pairings(hass)) == {other_hkid}


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

    async def _copy_pairing_with_backup(
        _hass: HomeAssistant,
        _source: dict[str, object],
        *,
        on_pairing_saved: object,
    ) -> tuple[dict[str, object], AccessoryInfo]:
        await on_pairing_saved(PAIRING_DATA)  # type: ignore[operator]
        return dict(PAIRING_DATA), info

    with patch(
        "custom_components.mysa_local.flows.thermostat.async_copy_pairing",
        AsyncMock(side_effect=_copy_pairing_with_backup),
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
    assert (await async_load_pending_pairings(hass))[HKID.lower()][
        "pairing"
    ] == PAIRING_DATA
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
    with pytest.raises(HomeKitMalformedPinError):
        format_pin(typed)


def test_format_pin_rejects_trivial_codes() -> None:
    """HomeKit never uses codes like 111-11-111."""
    with pytest.raises(InsecureCodeError):
        format_pin("1111 1111")


@pytest.mark.usefixtures("unpaired_controller")
async def test_pairing_retries_after_connection_error(hass: HomeAssistant) -> None:
    """A dozing thermostat doesn't end the flow; the user wakes it and retries."""
    real_start = HomeKitDiscovery.async_start_pairing
    calls = 0

    async def flaky_start(discovery: HomeKitDiscovery) -> object:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise HomeKitConnectionError("HAP server restarting")
        return await real_start(discovery)

    with patch.object(HomeKitDiscovery, "async_start_pairing", flaky_start):
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
