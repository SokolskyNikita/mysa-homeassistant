"""Commands keep their order when a HomeKit request finishes after fallback."""

import asyncio
from typing import Any
from unittest.mock import AsyncMock, patch

from homeassistant.exceptions import HomeAssistantError
import pytest

from custom_components.mysa_local.api import Mode, MysaConnectionError
from custom_components.mysa_local.local import TARGET_TEMPERATURE

from .common import HKID, hub_entry
from .conftest import setup_entry


@pytest.mark.parametrize("fail_first_reconciliation", [False, True])
async def test_late_local_write_reasserts_latest_cloud_command(
    hass, controller, cloud, fail_first_reconciliation
) -> None:
    """An older delayed device write cannot remain after a newer cloud command."""
    entry = hub_entry()
    await setup_entry(hass, entry)
    thermostat = next(iter(entry.runtime_data.thermostats.values()))
    pairing = controller.aliases[HKID]
    release = asyncio.Event()
    real_put = pairing.put_characteristics
    real_update = thermostat.cloud.async_update_state
    physical_setpoints: list[float] = []

    async def delayed_put(chars: Any) -> Any:
        await release.wait()
        physical_setpoints.append(chars[0][2])
        return await real_put(chars)

    async def cloud_write(device_id: str, changes: Any) -> None:
        await real_update(device_id, changes)
        physical_setpoints.append(changes["targetHeat"]["setpoint"])

    with (
        patch.object(pairing, "put_characteristics", delayed_put),
        patch.object(
            pairing, "get_characteristics", AsyncMock(side_effect=OSError("offline"))
        ),
        patch.object(thermostat.cloud, "async_update_state", cloud_write),
        patch("custom_components.mysa_local.thermostat.LOCAL_WRITE_TIMEOUT", 0.001),
    ):
        await thermostat.async_set_temperature(22)
        # A recovery event must not allow a second local write to overtake the
        # still-running request. Both newer commands go through the cloud.
        thermostat.local.mark_available()
        await thermostat.async_set_temperature(19)
        await thermostat.async_set_temperature(18)
        assert physical_setpoints == [22, 19, 18]
        if fail_first_reconciliation:
            with patch.object(
                thermostat.cloud,
                "async_update_state",
                AsyncMock(side_effect=HomeAssistantError("cloud unavailable")),
            ):
                release.set()
                await hass.async_block_till_done(wait_background_tasks=True)
                assert physical_setpoints[-1] == 22
                assert not thermostat.local_ok
            thermostat.notify()
        else:
            release.set()
        await hass.async_block_till_done(wait_background_tasks=True)

    assert physical_setpoints == [22, 19, 18, 22, 18]
    assert thermostat.target_temperature == 18
    assert thermostat._pending_local is None
    assert not thermostat._reconcile


async def test_simultaneous_local_commands_are_serialized(
    hass, controller, cloud
) -> None:
    """The second call cannot start a HomeKit request before the first settles."""
    entry = hub_entry()
    await setup_entry(hass, entry)
    thermostat = next(iter(entry.runtime_data.thermostats.values()))
    pairing = controller.aliases[HKID]
    first_started = asyncio.Event()
    release = asyncio.Event()
    writes: list[float] = []
    real_put = pairing.put_characteristics

    async def put(chars: Any) -> Any:
        writes.append(chars[0][2])
        if len(writes) == 1:
            first_started.set()
            await release.wait()
        return await real_put(chars)

    with patch.object(pairing, "put_characteristics", put):
        first = asyncio.create_task(thermostat.async_set_temperature(22))
        await first_started.wait()
        second = asyncio.create_task(thermostat.async_set_temperature(18))
        await asyncio.sleep(0)
        assert writes == [22]
        release.set()
        await asyncio.gather(first, second)
        await hass.async_block_till_done(wait_background_tasks=True)

    assert writes == [22, 18]
    assert thermostat.local.values[TARGET_TEMPERATURE] == 18
    assert cloud.updates == []


async def test_unload_closes_and_reconciles_pending_write(
    hass, controller, cloud
) -> None:
    """Close the session and drain the old request before abandoning routing."""
    entry = hub_entry()
    await setup_entry(hass, entry)
    thermostat = next(iter(entry.runtime_data.thermostats.values()))
    pairing = controller.aliases[HKID]
    release = asyncio.Event()
    real_put = pairing.put_characteristics

    async def delayed_put(chars: Any) -> Any:
        await release.wait()
        return await real_put(chars)

    with (
        patch.object(pairing, "put_characteristics", delayed_put),
        patch.object(
            pairing, "get_characteristics", AsyncMock(side_effect=OSError("offline"))
        ),
        patch("custom_components.mysa_local.thermostat.LOCAL_WRITE_TIMEOUT", 0.001),
    ):
        await thermostat.async_set_temperature(22)
        await thermostat.async_set_temperature(18)
        pending = thermostat._pending_local
        assert await hass.config_entries.async_unload(entry.entry_id)
        assert pending.cancelled()
        release.set()
        await hass.async_block_till_done(wait_background_tasks=True)
        assert thermostat.cloud_state.heat_setpoint == 18

    assert [item["targetHeat"]["setpoint"] for item in cloud.updates] == [22, 18, 18]


async def test_late_setpoint_reasserts_newer_off_command(
    hass, controller, cloud
) -> None:
    """A late temperature write must not undo a subsequently requested off mode."""
    entry = hub_entry()
    await setup_entry(hass, entry)
    thermostat = next(iter(entry.runtime_data.thermostats.values()))
    pairing = controller.aliases[HKID]
    release = asyncio.Event()
    real_put = pairing.put_characteristics

    async def delayed_put(chars: Any) -> Any:
        await release.wait()
        return await real_put(chars)

    with (
        patch.object(pairing, "put_characteristics", delayed_put),
        patch.object(
            pairing, "get_characteristics", AsyncMock(side_effect=OSError("offline"))
        ),
        patch("custom_components.mysa_local.thermostat.LOCAL_WRITE_TIMEOUT", 0.001),
    ):
        await thermostat.async_set_temperature(22)
        await thermostat.async_set_mode(Mode.OFF)
        release.set()
        await hass.async_block_till_done(wait_background_tasks=True)

    assert [item.get("modes") for item in cloud.updates] == [
        None,
        {"mode": 0},
        {"mode": 0},
    ]
    assert thermostat.mode == Mode.OFF


async def test_cloud_only_mode_supersedes_pending_local_mode(
    hass, controller, cloud
) -> None:
    """Fan-only has no HomeKit value, but still supersedes an older mode write."""
    entry = hub_entry()
    await setup_entry(hass, entry)
    thermostat = next(iter(entry.runtime_data.thermostats.values()))
    pairing = controller.aliases[HKID]
    release = asyncio.Event()
    real_put = pairing.put_characteristics

    async def delayed_put(chars: Any) -> Any:
        await release.wait()
        return await real_put(chars)

    with (
        patch.object(pairing, "put_characteristics", delayed_put),
        patch.object(
            pairing, "get_characteristics", AsyncMock(side_effect=OSError("offline"))
        ),
        patch("custom_components.mysa_local.thermostat.LOCAL_WRITE_TIMEOUT", 0.001),
    ):
        await thermostat.async_set_mode(Mode.OFF)
        await thermostat.async_set_mode(Mode.FAN_ONLY)
        release.set()
        await hass.async_block_till_done(wait_background_tasks=True)

    assert [item["modes"]["mode"] for item in cloud.updates] == [
        int(Mode.OFF),
        int(Mode.FAN_ONLY),
        int(Mode.FAN_ONLY),
    ]
    assert thermostat.mode == Mode.FAN_ONLY


async def test_multi_field_cloud_write_is_reconciled_once(
    hass, controller, cloud
) -> None:
    """A multi-field command gets one replay after an older local write."""
    entry = hub_entry()
    await setup_entry(hass, entry)
    thermostat = next(iter(entry.runtime_data.thermostats.values()))
    pairing = controller.aliases[HKID]
    release = asyncio.Event()
    real_put = pairing.put_characteristics

    async def delayed_put(chars: Any) -> Any:
        await release.wait()
        return await real_put(chars)

    changes = {"modes": {"mode": int(Mode.FAN_ONLY), "fan_mode": 3}}
    with (
        patch.object(pairing, "put_characteristics", delayed_put),
        patch.object(
            pairing, "get_characteristics", AsyncMock(side_effect=OSError("offline"))
        ),
        patch("custom_components.mysa_local.thermostat.LOCAL_WRITE_TIMEOUT", 0.001),
    ):
        await thermostat.async_set_temperature(22)
        await thermostat.async_cloud_update(changes)
        release.set()
        await hass.async_block_till_done(wait_background_tasks=True)

    assert len(cloud.updates) == 3
    assert [update.get("modes") for update in cloud.updates] == [
        None,
        changes["modes"],
        changes["modes"],
    ]
    assert thermostat.mode == Mode.FAN_ONLY


async def test_lost_cloud_response_preserves_latest_intent(
    hass, controller, cloud
) -> None:
    """An applied cloud write with a lost reply must survive an older local write."""
    entry = hub_entry()
    await setup_entry(hass, entry)
    thermostat = next(iter(entry.runtime_data.thermostats.values()))
    pairing = controller.aliases[HKID]
    release = asyncio.Event()
    real_put = pairing.put_characteristics
    real_update = thermostat.cloud.client.update_state
    lose_response = True

    async def delayed_put(chars: Any) -> Any:
        await release.wait()
        return await real_put(chars)

    async def update(device_id: str, changes: Any) -> None:
        nonlocal lose_response
        await real_update(device_id, changes)
        if changes["targetHeat"]["setpoint"] == 18 and lose_response:
            lose_response = False
            raise MysaConnectionError("lost response after applying command")

    with (
        patch.object(pairing, "put_characteristics", delayed_put),
        patch.object(
            pairing, "get_characteristics", AsyncMock(side_effect=OSError("offline"))
        ),
        patch.object(thermostat.cloud.client, "update_state", update),
        patch("custom_components.mysa_local.thermostat.LOCAL_WRITE_TIMEOUT", 0.001),
    ):
        await thermostat.async_set_temperature(22)
        with pytest.raises(HomeAssistantError):
            await thermostat.async_set_temperature(18)
        release.set()
        await hass.async_block_till_done(wait_background_tasks=True)

    assert [item["targetHeat"]["setpoint"] for item in cloud.updates] == [22, 18, 18]
    assert thermostat.target_temperature == 18
