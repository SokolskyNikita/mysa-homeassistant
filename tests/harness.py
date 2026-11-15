"""The Home Assistant test helpers, from whichever harness is installed.

Home Assistant 2026.11 ships its own harness, `pytest-homeassistant`, and the
older `pytest-homeassistant-custom-component` stops at 2026.10. CI tests the
oldest supported Home Assistant on the old one and the latest on the new one,
so tests import the helpers from here. Once the minimum supported version is
2026.11, import from `pytest_homeassistant` directly and delete this file.
"""

# ruff: noqa: F401

from importlib.util import find_spec

if find_spec("pytest_homeassistant") is not None:
    from pytest_homeassistant.common import (
        MockConfigEntry,
        MockUser,
        async_capture_events,
        async_fire_time_changed,
        get_system_health_info,
    )
    from pytest_homeassistant.components.diagnostics import (
        get_diagnostics_for_config_entry,
    )
    from pytest_homeassistant.test_util.aiohttp import (
        AiohttpClientMocker,
        AiohttpClientMockResponse,
    )
    from pytest_homeassistant.typing import ClientSessionGenerator
else:
    from pytest_homeassistant_custom_component.common import (
        MockConfigEntry,
        MockUser,
        async_capture_events,
        async_fire_time_changed,
        get_system_health_info,
    )
    from pytest_homeassistant_custom_component.components.diagnostics import (
        get_diagnostics_for_config_entry,
    )
    from pytest_homeassistant_custom_component.test_util.aiohttp import (
        AiohttpClientMocker,
        AiohttpClientMockResponse,
    )
    from pytest_homeassistant_custom_component.typing import ClientSessionGenerator
