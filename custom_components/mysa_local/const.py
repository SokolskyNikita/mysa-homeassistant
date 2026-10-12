"""Constants for the Mysa Local integration."""

from __future__ import annotations

from datetime import timedelta
import fnmatch
import logging
from typing import Final

DOMAIN: Final = "mysa_local"
LOGGER = logging.getLogger(__package__)

VERSION: Final = "0.6.0"
USER_AGENT: Final = (
    f"mysa-homeassistant/{VERSION} "
    "(+https://github.com/SokolskyNikita/mysa-homeassistant)"
)
WIFI_SETUP_URL: Final = "https://sokolskynikita.github.io/mysa-wifi-setup/"
ISSUES_URL: Final = "https://github.com/SokolskyNikita/mysa-homeassistant/issues"

SUBENTRY_THERMOSTAT: Final = "thermostat"

# Hub entry data (plus CONF_EMAIL, and CONF_PASSWORD only if the user chose to
# save it at sign-in)
CONF_REFRESH_TOKEN: Final = "refresh_token"  # noqa: S105
CONF_USER_ID: Final = "user_id"
# Sign-in form field: keep the password so an expired sign-in renews itself.
CONF_SAVE_PASSWORD: Final = "save_password"  # noqa: S105

# Hub entry options. CONF_MQTT_ENABLED is also the per-thermostat subentry
# override; a missing subentry value means enabled for backward compatibility.
CONF_CLOUD_ENABLED: Final = "cloud_enabled"
CONF_FIRMWARE_INSTALL: Final = "allow_firmware_install"
CONF_LIVE_MODE: Final = "live_mode"
CONF_MQTT_ENABLED: Final = "mqtt_enabled"
DEFAULT_LIVE_MODE: Final = True
DEFAULT_SCAN_INTERVAL: Final = 60
MIN_SCAN_INTERVAL: Final = 30
MAX_SCAN_INTERVAL: Final = 300

# Thermostat subentry data
CONF_DEVICE_ID: Final = "device_id"
CONF_MODEL: Final = "model"
CONF_PAIRING: Final = "pairing"
CONF_HEATER_WATTS: Final = "heater_watts"
CONF_CLOUD_RUNTIME: Final = "use_cloud_runtime"
CONF_MIGRATE_FROM: Final = "migrate_from_entry"

# Local (HomeKit) behaviour
LOCAL_WRITE_TIMEOUT: Final = 5.0
LOCAL_READ_TIMEOUT: Final = 3.0
LOCAL_POLL_INTERVAL: Final = timedelta(seconds=30)
LOCAL_DOWN_ISSUE_AFTER: Final = timedelta(minutes=15)

# Cloud polling of the slower-moving data
CLOUD_RECORDS_INTERVAL: Final = timedelta(minutes=10)
CLOUD_USAGE_INTERVAL: Final = timedelta(minutes=15)
CLOUD_HOLD_INTERVAL: Final = timedelta(minutes=5)
CLOUD_FIRMWARE_INTERVAL: Final = timedelta(hours=6)
CLOUD_CAPABILITIES_INTERVAL: Final = timedelta(hours=24)
CLOUD_REFRESH_AFTER_WRITE: Final = 2.0
CLOUD_WRITE_SPACING: Final = 0.5
CLOUD_MAX_BACKOFF: Final = timedelta(minutes=10)
# Outside live mode the thermostat samples every ~30 s but uploads them in
# batches 12-30 minutes apart, so the cloud's latest reading can be ~30 minutes
# old (two Lites, 2026-10-11). Allow a missed batch, but never treat an old
# shadow or an implausibly future-dated reading as live data.
CLOUD_TELEMETRY_MAX_AGE: Final = timedelta(minutes=90)
CLOUD_TELEMETRY_FUTURE_TOLERANCE: Final = timedelta(minutes=1)

# With the live mode option on (the default): while a paired thermostat's LAN
# path is down and the cloud stands in, ask Mysa for "live mode" (a reading every
# ~5 s instead of batches every 12-30 minutes) and poll faster. Never while the
# LAN path is healthy or the cloud is off.
CLOUD_FALLBACK_INTERVAL: Final = timedelta(seconds=10)
CLOUD_LIVE_MODE_TIMEOUT: Final = 300
CLOUD_LIVE_MODE_RENEW: Final = timedelta(seconds=240)

# Supported Mysa models that ship HomeKit, as glob patterns (mirrors
# manifest.json, where discovery of these goes to this integration): BB-V1-0,
# BB-V2-0-L, INF-V1-0, ... The Baseboard V3 (BB-V3), Central HVAC (ST) and
# Climate Sensor (CS) aren't supported, so their discovery is left alone.
HOMEKIT_MODEL_PATTERNS: Final = ("BB-V1-*", "BB-V2-*", "INF-V?-*", "AC-V?-*")


def is_mysa_model(model: str | None) -> bool:
    """True if a HomeKit model string is a Mysa thermostat."""
    return bool(model) and any(
        fnmatch.fnmatchcase(str(model), pattern) for pattern in HOMEKIT_MODEL_PATTERNS
    )


MANUFACTURER: Final = "Mysa"
HOMEKIT_MANUFACTURER: Final = "Empowered Homes Inc."

ATTR_UNTIL: Final = "until"
ATTR_EVENTS: Final = "events"

CONNECTION_LOCAL: Final = "local"
CONNECTION_CLOUD: Final = "cloud"
CONNECTION_OFFLINE: Final = "offline"

SIGNAL_CLOUD_UPDATED: Final = f"{DOMAIN}_cloud_updated"
