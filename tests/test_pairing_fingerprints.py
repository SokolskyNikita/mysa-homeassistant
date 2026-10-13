"""Identity-change recovery cleanup through real pairing flows and storage."""

import logging
from unittest.mock import AsyncMock, patch

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from mysa_client.homekit import HomeKitConnectionError
from mysa_client.homekit.device import HomeKitDevice
import pytest

from custom_components.mysa_local.const import DOMAIN
from custom_components.mysa_local.homekit_codes import homekit_codes
from custom_components.mysa_local.pairing_store import PairingStore, pairing_store

from .common import HKID, PAIRING_CODE, PAIRING_DATA, discovery


async def test_fingerprint_survives_restart_and_normalizes_code(
    hass: HomeAssistant,
) -> None:
    """A new store facade matches the code without persisting its plaintext."""
    fingerprint = await PairingStore(hass).async_code_fingerprint(PAIRING_CODE)
    assert fingerprint == await PairingStore(hass).async_code_fingerprint(
        PAIRING_CODE.replace("-", "")
    )
    assert fingerprint != await PairingStore(hass).async_code_fingerprint("234-56-789")
    assert PAIRING_CODE not in fingerprint


@pytest.mark.usefixtures("unpaired_controller")
@pytest.mark.parametrize("interrupted", [False, True])
async def test_new_identity_retires_only_matching_code_after_success(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture, interrupted: bool
) -> None:
    """Keep backups through failure; successful pairing/resume retires matches."""
    store = pairing_store(hass)
    fingerprint = await store.async_code_fingerprint(PAIRING_CODE)
    old_ids = ["11:11:11:11:11:11", "22:22:22:22:22:22"]
    other_id, legacy_id = "33:33:33:33:33:33", "44:44:44:44:44:44"
    for hkid, code_hash in [
        *[(old_id, fingerprint) for old_id in old_ids],
        (other_id, await store.async_code_fingerprint("234-56-789")),
        (legacy_id, None),
    ]:
        await store.async_save(
            hkid,
            {**PAIRING_DATA, "AccessoryPairingID": hkid},
            name="Mysa",
            model="BB-V2-0-L",
            code_fingerprint=code_hash,
        )
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_HOMEKIT}, data=discovery()
    )
    caplog.set_level(logging.INFO, logger="custom_components.mysa_local")
    with patch(
        "custom_components.mysa_local.flows.thermostat.async_unpair",
        new_callable=AsyncMock,
    ) as unpair:
        if interrupted:
            with patch.object(
                HomeKitDevice,
                "async_get_info",
                side_effect=HomeKitConnectionError("device restarted"),
            ):
                result = await hass.config_entries.flow.async_configure(
                    result["flow_id"], {"pairing_code": PAIRING_CODE}
                )
            assert result["errors"] == {"base": "cannot_connect_device"}
            pending = await store.async_load()
            assert set(old_ids) <= pending.keys()
            assert pending[HKID.lower()]["code_fingerprint"] == fingerprint
            assert "Retired" not in caplog.text
            # Resume from the durable backup in a new flow after cancellation.
            hass.config_entries.flow.async_abort(result["flow_id"])
            result = await hass.config_entries.flow.async_init(
                DOMAIN,
                context={"source": config_entries.SOURCE_HOMEKIT},
                data=discovery(paired=True),
            )
        else:
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"], {"pairing_code": PAIRING_CODE}
            )
        assert result["step_id"] == "cloud_offer"
        unpair.assert_not_awaited()
    pending = await store.async_load()
    assert set(pending) == {HKID.lower(), other_id, legacy_id}
    assert pending[HKID.lower()]["pairing"] == PAIRING_DATA
    assert pending[HKID.lower()]["code_fingerprint"] == fingerprint
    assert await homekit_codes(hass).async_count() == 0
    assert "Retired 2 pending HomeKit recovery record(s)" in caplog.text
    integration_logs = "\n".join(
        record.getMessage()
        for record in caplog.records
        if record.name.startswith("custom_components.mysa_local")
    )
    assert fingerprint not in integration_logs
    assert PAIRING_CODE not in integration_logs
    hass.config_entries.flow.async_abort(result["flow_id"])
