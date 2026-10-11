"""Constants for the Mysa cloud API.

The Cognito IDs are app-level values that ship inside Mysa's own app. They are
not secrets and are the same for every user.
"""

from __future__ import annotations

from enum import IntEnum, StrEnum
from typing import Final

COGNITO_REGION: Final = "us-east-1"
COGNITO_USER_POOL_ID: Final = "us-east-1_GUFWfhI7g"
COGNITO_CLIENT_ID: Final = "6cktj934gasnc72f7jo2cmf6rt"
COGNITO_URL: Final = f"https://cognito-idp.{COGNITO_REGION}.amazonaws.com/"

API_BASE: Final = "https://mysa-backend.mysa.cloud"
# The older host, used here only for purgeadd (only Mysa's older app had it,
# and it sent it here). Mysa's current app sends even its legacy routes to
# API_BASE while the MONOLITH_API_ACCESS feature flag is on.
LEGACY_API_BASE: Final = "https://app-prod.mysa.cloud"

# Every write through /state/{dev}/update carries a source. 3 is what Mysa's
# own app sends; the change log reports such changes as "app".
WRITE_SOURCE: Final = 3

# /mobile/bootstrap refuses requests without the app version it serves (400,
# FST_ERR_VALIDATION). This is the app version whose API this client mirrors.
APP_VERSION: Final = "4.20.1"

# Refresh the ID token this many seconds before it expires.
TOKEN_REFRESH_MARGIN: Final = 300

REQUEST_TIMEOUT: Final = 20


class Mode(IntEnum):
    """Climate modes as used in the `modes` section of the device state."""

    OFF = 0
    AUTO = 1
    COOL = 3
    HEAT = 4
    EMERGENCY_HEAT = 5
    PRE_COOL = 6
    FAN_ONLY = 7
    DRY = 8
    SLEEP = 9


# Names used by the capability document (features.climateControl.mode).
MODE_BY_NAME: Final[dict[str, Mode]] = {
    "off": Mode.OFF,
    "auto": Mode.AUTO,
    "cool": Mode.COOL,
    "heat": Mode.HEAT,
    "emergencyHeat": Mode.EMERGENCY_HEAT,
    "preCool": Mode.PRE_COOL,
    "fanOnly": Mode.FAN_ONLY,
    "dry": Mode.DRY,
    "sleep": Mode.SLEEP,
}

# State section that holds the setpoint for a mode.
SETPOINT_SECTION_BY_MODE: Final[dict[Mode, str]] = {
    Mode.HEAT: "targetHeat",
    Mode.EMERGENCY_HEAT: "targetHeat",
    Mode.COOL: "targetCool",
}


class ButtonLock(IntEnum):
    """Values of physicalInterface.lockout."""

    UNLOCKED = 0
    LOCKED = 3


class HeaterType(IntEnum):
    """Baseboard switching profile (bbConfig.controlType / DutyCycleOpt)."""

    FAN_FORCED = 8
    BASEBOARD = 9


class DutyCycleOpt(IntEnum):
    """Every heater profile Mysa's app knows. HeaterType is the BB-V2 part."""

    BB1_BASEBOARD = 0
    BB1_RADIANT = 4
    BB1_FAN_FORCED_SHORT = 5
    BB1_FAN_FORCED_MEDIUM = 6
    BB1_FAN_FORCED_LONG = 7
    BB2_FAN_FORCED = 8
    BB2_BASEBOARD = 9


class ModelFamily(StrEnum):
    """Mysa's device families (the app's DEVICES)."""

    BASEBOARD_V1 = "baseboard"
    BASEBOARD_V2 = "baseboard2"
    BASEBOARD_LITE = "baseboard2lite"
    BASEBOARD_V3 = "baseboard3"
    INFLOOR = "infloor"
    AC = "ac"
    CENTRAL_HVAC = "hvac"
    CLIMATE_SENSOR = "cs"


# Every model string Mysa's app 4.20.1 accepts (its hwRev table, which also
# lists lowercase copies; compare uppercased).
MODEL_FAMILIES: Final[dict[str, ModelFamily]] = {
    **dict.fromkeys(
        ("BB-V1-0", "V1-0", "BB-V1-1", "V1-1", "V1"), ModelFamily.BASEBOARD_V1
    ),
    **dict.fromkeys(("BB-V2-0", "V2-0", "V2", "BB-V2"), ModelFamily.BASEBOARD_V2),
    **dict.fromkeys(
        ("BB-V2-0-L", "BB-V2-0-LTE", "V2-0-L", "V2-0-LTE", "V2-L"),
        ModelFamily.BASEBOARD_LITE,
    ),
    "BB-V3-0": ModelFamily.BASEBOARD_V3,
    **dict.fromkeys(("INF-V1-0", "INF-V1"), ModelFamily.INFLOOR),
    **dict.fromkeys(("AC-V1-0", "AC-V1"), ModelFamily.AC),
    **dict.fromkeys(("ST-V1-0", "ST-V1", "LV"), ModelFamily.CENTRAL_HVAC),
    **dict.fromkeys(("CS-V1-0", "CS-V1"), ModelFamily.CLIMATE_SENSOR),
}

# Families that join with Wi-Fi details alone, so add_device can put them on an
# account. Baseboard V3, Central HVAC and the Climate Sensor also need a
# credential file over Bluetooth, an update-fleet registration and a finalize
# step; this client deliberately doesn't add those.
WIFI_ONLY_FAMILIES: Final = frozenset(
    {
        ModelFamily.BASEBOARD_V1,
        ModelFamily.BASEBOARD_V2,
        ModelFamily.BASEBOARD_LITE,
        ModelFamily.INFLOOR,
        ModelFamily.AC,
    }
)


def model_family(model: str | None) -> ModelFamily | None:
    """The family of a model string, or None when Mysa's app doesn't know it."""
    return MODEL_FAMILIES.get(model.strip().upper()) if model else None


class ChangeSource(StrEnum):
    """Where a change in the state history came from."""

    DEVICE = "device"
    APP = "app"
    SCHEDULE = "schedule"
    SHORTCUTS = "shortcuts"


# Known model strings and what they are. Only BB-V2-0-L has been tested.
MODEL_NAMES: Final[dict[str, str]] = {
    "BB-V1-0": "Mysa Baseboard V1",
    "BB-V1-1": "Mysa Baseboard V1",
    "BB-V2-0": "Mysa Baseboard V2",
    "BB-V2-0-L": "Mysa Baseboard Lite",
    "BB-V2-0-LTE": "Mysa Baseboard Lite",
    "BB-V3-0": "Mysa Baseboard V3",
    "INF-V1-0": "Mysa In-Floor",
    "AC-V1-0": "Mysa for Mini-Split AC",
    "ST-V1-0": "Mysa for Central HVAC",
    "LV": "Mysa for Central HVAC",
    "CS-V1-0": "Mysa Climate Sensor",
}
TESTED_MODELS: Final = frozenset({"BB-V2-0-L"})
