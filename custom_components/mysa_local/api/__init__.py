"""Standalone async client for Mysa's cloud API.

Nothing in this package imports Home Assistant, so it can move to its own PyPI
package later.
"""

from .auth import MysaAuth, Tokens, jwt_claims
from .client import MysaClient, iso_utc
from .const import (
    MODEL_FAMILIES,
    MODEL_NAMES,
    TESTED_MODELS,
    WIFI_ONLY_FAMILIES,
    ButtonLock,
    ChangeSource,
    DutyCycleOpt,
    HeaterType,
    Mode,
    ModelFamily,
    model_family,
)
from .exceptions import (
    MysaApiError,
    MysaAuthError,
    MysaConnectionError,
    MysaError,
    MysaInvalidCredentialsError,
    MysaNotFoundError,
    MysaRateLimitError,
    MysaUnsupportedChallengeError,
)
from .models import (
    Bootstrap,
    Capabilities,
    DeviceRecord,
    DeviceState,
    FirmwareInfo,
    StateChange,
    UsageBucket,
    UsageDay,
    value_range,
)

__all__ = [
    "MODEL_FAMILIES",
    "MODEL_NAMES",
    "TESTED_MODELS",
    "WIFI_ONLY_FAMILIES",
    "Bootstrap",
    "ButtonLock",
    "Capabilities",
    "ChangeSource",
    "DeviceRecord",
    "DeviceState",
    "DutyCycleOpt",
    "FirmwareInfo",
    "HeaterType",
    "Mode",
    "ModelFamily",
    "MysaApiError",
    "MysaAuth",
    "MysaAuthError",
    "MysaClient",
    "MysaConnectionError",
    "MysaError",
    "MysaInvalidCredentialsError",
    "MysaNotFoundError",
    "MysaRateLimitError",
    "MysaUnsupportedChallengeError",
    "StateChange",
    "Tokens",
    "UsageBucket",
    "UsageDay",
    "iso_utc",
    "jwt_claims",
    "model_family",
    "value_range",
]
