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
    "BB-V3-0": "Mysa Baseboard V3",
    "INF-V1-0": "Mysa In-Floor",
    "AC-V1-0": "Mysa for Mini-Split AC",
    "ST-V1-0": "Mysa for Central HVAC",
    "CS-V1-0": "Mysa Climate Sensor",
}
TESTED_MODELS: Final = frozenset({"BB-V2-0-L"})
