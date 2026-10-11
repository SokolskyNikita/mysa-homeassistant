"""Standalone async client for Mysa's cloud API.

Nothing in this package imports Home Assistant, so it can move to its own PyPI
package later.
"""

from .auth import MysaAuth, Tokens, jwt_claims
from .client import MysaClient, iso_utc
from .const import (
    MODEL_NAMES,
    TESTED_MODELS,
    ButtonLock,
    ChangeSource,
    HeaterType,
    Mode,
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
    "MODEL_NAMES",
    "TESTED_MODELS",
    "Bootstrap",
    "ButtonLock",
    "Capabilities",
    "ChangeSource",
    "DeviceRecord",
    "DeviceState",
    "FirmwareInfo",
    "HeaterType",
    "Mode",
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
    "value_range",
]
