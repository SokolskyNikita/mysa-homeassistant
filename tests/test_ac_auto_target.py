"""A Mini-Split AC's single heat/cool target (mysa-client 0.4.0).

Mysa's app writes it as ``targetAuto.setpoint``; mysa-client reads it back as
``DeviceState.auto_setpoint``. Neither is verified on a device here, so the
card offers the target in heat/cool only while the cloud reports one.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.climate import ClimateEntityFeature
from homeassistant.const import ATTR_SUPPORTED_FEATURES
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from mysa_client.cloud import Mode
import pytest

from .common import DEVICE_ID, FakeCloud, hub_entry, setpoint
from .conftest import setup_entry
from .test_temperature_units import (
    attrs,
    cloud_only,
    report,
    set_temperature,
    use_units,
)


def ac(cloud: FakeCloud, mode: int, **sections: Any) -> Any:
    """A cloud-only Mini-Split AC whose remote offers 18-28 °C in auto."""
    record = {
        "Model": "AC-V1-0",
        # Keys are the remote's modes: 2 is auto, 3 heat and 4 cool.
        "SupportedModes": {
            "2": {"temperatures": list(range(18, 29))},
            "3": {"temperatures": list(range(16, 31))},
            "4": {"temperatures": list(range(16, 31))},
        },
    }
    cloud.devices["DevicesObj"][DEVICE_ID].update(record)
    cloud.bootstrap["data"]["devices"][DEVICE_ID].update(record)
    cloud.capabilities["features"]["climateControl"]["mode"]["validValues"] = [
        "off",
        "auto",
        "cool",
        "heat",
    ]
    report(cloud, "modes", mode=mode)
    for section, value in sections.items():
        cloud.state[section] = {
            "reported": {"setpoint": value},
            "desired": {"setpoint": value},
        }
    return cloud_only()


def has_target(hass: HomeAssistant) -> bool:
    features = ClimateEntityFeature(attrs(hass)[ATTR_SUPPORTED_FEATURES])
    return ClimateEntityFeature.TARGET_TEMPERATURE in features


async def test_ac_auto_target_is_written_as_target_auto(
    hass: HomeAssistant, cloud: FakeCloud
) -> None:
    """Switching an AC to heat/cool with a target writes targetAuto.setpoint."""
    use_units(hass, False)
    entry = ac(cloud, Mode.HEAT, targetHeat=21)
    await setup_entry(hass, entry)
    assert has_target(hass)
    await set_temperature(hass, hvac_mode="heat_cool", temperature=24)
    assert cloud.updates[-1]["targetAuto"] == {"setpoint": 24.0}
    assert all("targetHeat" not in update for update in cloud.updates)
    # The cloud now reports the auto target, so the card offers it.
    await hass.config_entries.async_get_entry(
        entry.entry_id
    ).runtime_data.cloud.async_refresh()
    await hass.async_block_till_done()
    assert has_target(hass)
    assert attrs(hass)["temperature"] == 24
    # The auto range from the remote's list, in its whole-degree step.
    assert (attrs(hass)["min_temp"], attrs(hass)["max_temp"]) == (18, 28)
    assert attrs(hass)["target_temp_step"] == 1


async def test_ac_auto_target_is_clamped_to_the_auto_range(
    hass: HomeAssistant, cloud: FakeCloud
) -> None:
    """The rules use the AC's auto range, not its heat range, in heat/cool."""
    use_units(hass, False)
    await setup_entry(hass, ac(cloud, Mode.AUTO, targetAuto=22))
    await set_temperature(hass, temperature=27.6)
    assert cloud.updates[-1]["targetAuto"] == {"setpoint": 28.0}


async def test_ac_auto_hides_the_target_until_reported(
    hass: HomeAssistant, cloud: FakeCloud
) -> None:
    """Without a reported auto target the card shows none, as before 0.9.2."""
    await setup_entry(hass, ac(cloud, Mode.AUTO))
    assert not has_target(hass)


@pytest.mark.usefixtures("controller")
async def test_heat_cool_target_on_a_heater_is_a_translated_refusal(
    hass: HomeAssistant, cloud: FakeCloud
) -> None:
    """The encoder's refusal reaches the caller as a ServiceValidationError."""
    entry = hub_entry()
    await setup_entry(hass, entry)
    coordinator = entry.runtime_data.cloud
    sent = len(cloud.updates)
    with pytest.raises(ServiceValidationError) as err:
        await coordinator.async_set_temperature(
            DEVICE_ID, Mode.AUTO, setpoint(22, Mode.HEAT)
        )
    assert err.value.translation_key == "setpoint_mode_unsupported"
    assert len(cloud.updates) == sent
