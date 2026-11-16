"""The climate card's bounds and step for every model, pinned.

mysa-client 0.4.0 added ``SetpointKind.AUTO``; Central HVAC builds its card
envelope from the setpoint kinds, so a new kind must not move any bound.
These values are the ones Mysa Local 0.9.1 showed on mysa-client 0.3.0.
"""

from __future__ import annotations

from homeassistant.core import HomeAssistant
import pytest

from .common import FakeCloud, hub_entry
from .conftest import setup_entry
from .test_new_cloud_models import configure
from .test_temperature_units import ac_in_cool, attrs, cloud_only, use_units


@pytest.mark.parametrize(
    ("model", "fahrenheit", "expected"),
    [
        ("BB-V2-0-L", False, (5, 30, 0.5)),
        ("BB-V2-0-L", True, (41, 86, 1)),
        ("BB-V3-0", False, (5, 30, 0.5)),
        ("BB-V3-0", True, (41, 86, 1)),
        ("ST-V1-0", False, (10, 35, 0.5)),
        ("ST-V1-0", True, (50, 95, 1)),
        ("AC-V1-0", False, (16, 30, 1)),
        ("AC-V1-0", True, (60, 86, 1)),
    ],
)
async def test_card_range_and_step_per_model(
    hass: HomeAssistant,
    cloud: FakeCloud,
    model: str,
    fahrenheit: bool,
    expected: tuple[float, float, float],
) -> None:
    """min_temp, max_temp and target_temp_step for each model's fixture."""
    use_units(hass, fahrenheit)
    if model == "BB-V2-0-L":
        entry = cloud_only()
    elif model == "AC-V1-0":
        entry = ac_in_cool(cloud, 23)
    else:
        entry = hub_entry(subentries=[configure(cloud, model)])
    await setup_entry(hass, entry)
    state = attrs(hass)
    assert (
        state["min_temp"],
        state["max_temp"],
        state["target_temp_step"],
    ) == expected
