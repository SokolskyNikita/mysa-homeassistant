"""Fixtures for Mysa Local tests."""

from __future__ import annotations

from collections.abc import AsyncGenerator, Generator
from contextlib import ExitStack
from unittest.mock import AsyncMock, MagicMock, patch

from aiohomekit.testing import FakeController
from homeassistant.core import HomeAssistant
from mysa_client.homekit import HomeKitProtocol
from mysa_client.mqtt import MqttConnectionError
import pytest

from .common import FakeCloud, hub_entry, make_controller
from .harness import AiohttpClientMocker, MockConfigEntry


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Load custom_components/ in every test."""


@pytest.fixture(autouse=True)
def no_cloud_write_spacing() -> Generator[None]:
    """Don't really wait between cloud writes.

    The coordinator spaces writes 0.5 s apart; slept for real, that was most
    of the suite's run time. No test depends on the wait itself.
    """
    with patch("custom_components.mysa_local.cloud_coordinator.CLOUD_WRITE_SPACING", 0):
        yield


@pytest.fixture(autouse=True)
def mock_zc(mock_async_zeroconf: object) -> None:
    """Never touch the real network for mDNS."""


@pytest.fixture(autouse=True)
def mqtt_client_factory(request: pytest.FixtureRequest) -> Generator[MagicMock]:
    """Keep the optional cloud push connection offline unless a test enables it."""
    with ExitStack() as stack:
        factory = stack.enter_context(
            patch("custom_components.mysa_local.mqtt_coordinator.MysaMqttClient")
        )
        if not request.node.get_closest_marker("mqtt_runtime"):
            # Older tests wait for all HA background tasks to finish. The
            # persistent stream never completes; lifecycle tests opt in.
            stack.enter_context(
                patch(
                    "custom_components.mysa_local.mqtt_coordinator."
                    "MysaMqttCoordinator.async_start"
                )
            )
        factory.return_value.connected = False
        factory.return_value.connect = AsyncMock(
            side_effect=MqttConnectionError("MQTT offline in test")
        )
        factory.return_value.close = AsyncMock()
        yield factory


@pytest.fixture
async def drain_background_tasks(
    hass: HomeAssistant, mqtt_client_factory: MagicMock
) -> AsyncGenerator[None]:
    """Finish reloads a flow left running before the test's patches are undone.

    A flow that creates or changes a (sub)entry schedules an entry reload and
    may return before it finishes. Under load the reload then ran during
    teardown, after mqtt_client_factory had stopped patching the MQTT start,
    and left the real MQTT task lingering. Depending on both fixtures makes
    this run first at teardown, while the patches still apply.
    """
    yield
    await hass.async_block_till_done(wait_background_tasks=True)


@pytest.fixture
def cloud(aioclient_mock: AiohttpClientMocker) -> FakeCloud:
    """The simulated Mysa cloud."""
    return FakeCloud(aioclient_mock)


@pytest.fixture
async def controller() -> AsyncGenerator[FakeController]:
    """A fake aiohomekit controller with the Lite paired."""
    ctrl = await make_controller(paired=True)
    protocol = HomeKitProtocol.from_controller(ctrl)
    with (
        patch(
            "custom_components.mysa_local.async_get_controller",
            AsyncMock(return_value=protocol),
        ),
        patch(
            "custom_components.mysa_local.flows.thermostat.async_get_controller",
            AsyncMock(return_value=protocol),
        ),
        patch(
            "custom_components.mysa_local.homekit_adapter.async_get_controller",
            AsyncMock(return_value=protocol),
        ),
    ):
        yield ctrl


@pytest.fixture
def unpaired_controller() -> Generator[None]:
    """Patch in a controller that only has an unpaired, discoverable Lite."""
    holder: dict[str, HomeKitProtocol] = {}

    async def _get(hass: HomeAssistant) -> HomeKitProtocol:
        if "c" not in holder:
            controller = await make_controller(paired=False, unpaired=True)
            holder["c"] = HomeKitProtocol.from_controller(controller)
        return holder["c"]

    with (
        patch("custom_components.mysa_local.async_get_controller", _get),
        patch(
            "custom_components.mysa_local.flows.thermostat.async_get_controller", _get
        ),
        patch(
            "custom_components.mysa_local.homekit_adapter.async_get_controller", _get
        ),
    ):
        yield


@pytest.fixture
def entry() -> MockConfigEntry:
    """The default entry: signed in, cloud on, one paired thermostat."""
    return hub_entry()


async def setup_entry(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Add and set up an entry."""
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
