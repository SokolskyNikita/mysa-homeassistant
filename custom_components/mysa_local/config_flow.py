"""Config flow: one Mysa entry, each thermostat a subentry.

Thermostats join by HomeKit pairing (discovered or picked from a list), by
moving them over from the HomeKit Device integration, or, for thermostats with
no local path, from the Mysa account.
"""

from __future__ import annotations

from collections.abc import Mapping
import re
from typing import Any, override

import aiohomekit
from aiohomekit.controller.abstract import (
    AbstractDiscovery,
    AbstractPairing,
    FinishPairing,
)
from aiohomekit.exceptions import AuthenticationError, HomeKitException
from aiohomekit.utils import domain_to_name
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentry,
    ConfigSubentryData,
    ConfigSubentryFlow,
    OptionsFlow,
    SubentryFlowResult,
)
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD, CONF_SCAN_INTERVAL
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import AbortFlow, FlowHandler, section
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    BooleanSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo
import voluptuous as vol

from .api import (
    MODEL_NAMES,
    TESTED_MODELS,
    Bootstrap,
    MysaAuth,
    MysaAuthError,
    MysaClient,
    MysaConnectionError,
    MysaError,
    MysaInvalidCredentialsError,
    MysaUnsupportedChallengeError,
)
from .const import (
    CONF_CLOUD_ENABLED,
    CONF_CLOUD_RUNTIME,
    CONF_DEVICE_ID,
    CONF_FIRMWARE_INSTALL,
    CONF_HEATER_WATTS,
    CONF_MIGRATE_FROM,
    CONF_MODEL,
    CONF_PAIRING,
    CONF_REFRESH_TOKEN,
    CONF_USER_ID,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    HOMEKIT_MANUFACTURER,
    LOGGER,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
    SUBENTRY_THERMOSTAT,
    USER_AGENT,
    WIFI_SETUP_URL,
    is_mysa_model,
)
from .homekit import ENDPOINT_KEYS, async_copy_pairing, async_get_controller
from .local import CONNECTION_ERRORS, AccessoryInfo, parse_accessories
from .migration import HKC_DOMAIN

PIN_FORMAT = re.compile(r"^(\d{3})-?(\d{2})-?(\d{3})$")
INSECURE_CODES = {
    "00000000",
    "11111111",
    "22222222",
    "33333333",
    "44444444",
    "55555555",
    "66666666",
    "77777777",
    "88888888",
    "99999999",
    "12345678",
    "87654321",
}
CONF_PAIRING_CODE = "pairing_code"
CONF_DEVICE = "device"
CONF_ENTRY = "entry"
CONF_DEVICES = "devices"


def format_pin(pin: str) -> str:
    """Accept 12345678 or 123-45-678 and return 123-45-678."""
    match = PIN_FORMAT.match(pin.strip())
    if not match or "".join(match.groups()) in INSECURE_CODES:
        raise aiohomekit.exceptions.MalformedPinError(pin)
    return "-".join(match.groups())


def hub_entry(hass: HomeAssistant) -> ConfigEntry | None:
    """The single Mysa Local entry, if it exists."""
    for entry in hass.config_entries.async_entries(DOMAIN, include_ignore=False):
        return entry
    return None


def configured_hkids(entry: ConfigEntry | None) -> dict[str, str]:
    """HomeKit IDs (lowercase) of thermostats already in the entry -> subentry ID."""
    if entry is None:
        return {}
    result = {}
    for subentry in entry.subentries.values():
        pairing = subentry.data.get(CONF_PAIRING) or {}
        if hkid := pairing.get("AccessoryPairingID"):
            result[hkid.lower()] = subentry.subentry_id
    return result


def homekit_device_candidates(hass: HomeAssistant) -> dict[str, str]:
    """HomeKit Device entries that are Mysa thermostats: entry ID -> title."""
    dev_reg = dr.async_get(hass)
    candidates = {}
    for entry in hass.config_entries.async_entries(HKC_DOMAIN, include_ignore=False):
        if not entry.data.get("AccessoryPairingID"):
            continue
        for device in dr.async_entries_for_config_entry(dev_reg, entry.entry_id):
            if device.manufacturer == HOMEKIT_MANUFACTURER or is_mysa_model(
                device.model
            ):
                candidates[entry.entry_id] = entry.title
                break
    return candidates


def thermostat_subentry(
    info: AccessoryInfo,
    pairing: Mapping[str, Any] | None,
    title: str,
    migrate_from: str | None = None,
    *,
    device_id: str | None = None,
    model: str | None = None,
) -> ConfigSubentryData:
    """Build the subentry for one thermostat."""
    if device_id is None:
        hkid = (pairing or {}).get("AccessoryPairingID", "")
        device_id = info.device_id or "hk" + re.sub(r"[^0-9a-f]", "", hkid.lower())
    data: dict[str, Any] = {
        CONF_DEVICE_ID: device_id,
        CONF_MODEL: model or info.model,
        CONF_HEATER_WATTS: 0,
        CONF_CLOUD_RUNTIME: True,
    }
    if pairing is not None:
        data[CONF_PAIRING] = dict(pairing)
    if migrate_from:
        data[CONF_MIGRATE_FROM] = migrate_from
    return ConfigSubentryData(
        data=data,
        subentry_type=SUBENTRY_THERMOSTAT,
        title=title,
        unique_id=device_id,
    )


async def async_sign_in(
    hass: HomeAssistant, email: str, password: str
) -> tuple[str, str | None, Bootstrap]:
    """Sign in to Mysa; return (refresh token, user ID, account overview)."""
    session = async_get_clientsession(hass)
    auth = MysaAuth(session, user_agent=USER_AGENT)
    await auth.sign_in(email, password)
    client = MysaClient(session, auth, user_agent=USER_AGENT)
    try:
        bootstrap = await client.bootstrap()
    except MysaAuthError:
        raise
    except MysaError as err:
        # Signed in fine; only the account overview failed. The device list
        # has what setup needs.
        LOGGER.warning("Mysa bootstrap failed (%s); using the device list", err)
        bootstrap = Bootstrap(
            user_id=None, home_ids=(), devices=await client.get_devices()
        )
    token = auth.refresh_token
    if token is None:  # pragma: no cover - sign_in always stores one
        raise MysaError("No refresh token")
    user_id = bootstrap.user_id or auth.claims.get("sub")
    return token, user_id, bootstrap


def sign_in_error(err: Exception) -> str:
    """Map a sign-in exception to a form error key (and log the odd ones)."""
    if isinstance(err, MysaInvalidCredentialsError):
        return "invalid_auth"
    if isinstance(err, MysaUnsupportedChallengeError):
        LOGGER.warning("Mysa sign-in needs an unsupported step: %s", err)
        return "mfa_unsupported"
    if isinstance(err, MysaConnectionError):
        LOGGER.warning("Couldn't reach Mysa to sign in: %s", err)
        return "cannot_connect"
    LOGGER.error("Mysa sign-in failed: %s", err)
    return "unknown"


CREDENTIALS_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_EMAIL): TextSelector(
            TextSelectorConfig(type=TextSelectorType.EMAIL, autocomplete="username")
        ),
        vol.Required(CONF_PASSWORD): TextSelector(
            TextSelectorConfig(
                type=TextSelectorType.PASSWORD, autocomplete="current-password"
            )
        ),
    }
)


class ThermostatSteps(FlowHandler[Any, Any, Any]):
    """Pairing, picking and moving thermostats; shared by both flows."""

    _hkid: str | None = None
    _name: str | None = None
    _model: str | None = None
    _finish_pairing: FinishPairing | None = None
    _pairing_in_progress = False
    _discoveries: dict[str, AbstractDiscovery]
    _takeover_entry_id: str | None = None

    # Implemented by each flow.
    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> Any:
        """First step (menu)."""
        raise NotImplementedError

    async def _async_thermostat_ready(
        self,
        info: AccessoryInfo,
        pairing: Mapping[str, Any],
        migrate_from: str | None = None,
    ) -> Any:
        raise NotImplementedError

    def _known_hkids(self) -> dict[str, str]:
        return configured_hkids(hub_entry(self.hass))

    async def async_step_wifi_help(
        self, user_input: dict[str, Any] | None = None
    ) -> Any:
        """Explain how to put a thermostat on Wi-Fi first."""
        if user_input is not None:
            return await self.async_step_user()
        return self.async_show_form(
            step_id="wifi_help",
            data_schema=vol.Schema({}),
            description_placeholders={"url": WIFI_SETUP_URL},
        )

    async def async_step_pick(self, user_input: dict[str, Any] | None = None) -> Any:
        """Pick an unpaired Mysa found on the network."""
        controller = await async_get_controller(self.hass)
        if user_input is not None and user_input.get(CONF_DEVICE) in getattr(
            self, "_discoveries", {}
        ):
            discovery = self._discoveries[user_input[CONF_DEVICE]]
            self._hkid = discovery.description.id.lower()
            self._name = discovery.description.name
            self._model = getattr(discovery.description, "model", None)
            return await self.async_step_pair()

        known = self._known_hkids()
        self._discoveries = {}
        async for discovery in controller.async_discover():
            description = discovery.description
            model = getattr(description, "model", None)
            if not is_mysa_model(model) or discovery.paired:
                continue
            if description.id.lower() in known:
                continue
            self._discoveries[description.id.lower()] = discovery

        if not self._discoveries:
            return self.async_show_form(
                step_id="pick",
                data_schema=vol.Schema({}),
                errors={"base": "no_devices"},
                description_placeholders={"url": WIFI_SETUP_URL},
            )
        options = [
            SelectOptionDict(
                value=hkid,
                label=f"{d.description.name} ({MODEL_NAMES.get(d.description.model, d.description.model)})",  # type: ignore[attr-defined]
            )
            for hkid, d in self._discoveries.items()
        ]
        return self.async_show_form(
            step_id="pick",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_DEVICE): SelectSelector(
                        SelectSelectorConfig(options=options)
                    )
                }
            ),
            description_placeholders={"url": WIFI_SETUP_URL},
        )

    def _show_pair_form(
        self,
        errors: dict[str, str] | None = None,
        error_detail: str = "",
    ) -> Any:
        model = self._model or ""
        return self.async_show_form(
            step_id="pair",
            data_schema=vol.Schema({vol.Required(CONF_PAIRING_CODE): str}),
            errors=errors or {},
            description_placeholders={
                "name": self._name or "Mysa",
                "model": MODEL_NAMES.get(model, model) or "Mysa",
                "url": WIFI_SETUP_URL,
                "error": error_detail,
            },
        )

    async def _async_start_pairing(self) -> tuple[str | None, str]:
        """Open a pair-setup session; return (error key, detail)."""
        assert self._hkid is not None
        controller = await async_get_controller(self.hass)
        try:
            discovery = await controller.async_find(self._hkid)
            self._finish_pairing = await discovery.async_start_pairing(self._hkid)
        except aiohomekit.BusyError:
            return "busy", ""
        except aiohomekit.MaxTriesError:
            return "max_tries", ""
        except aiohomekit.UnavailableError as err:
            raise AbortFlow("already_paired") from err
        except (
            aiohomekit.AccessoryNotFoundError,
            aiohomekit.AccessoryDisconnectedError,
        ) as err:
            raise AbortFlow("cannot_connect") from err
        except (HomeKitException, IndexError) as err:
            LOGGER.debug("Starting pairing failed: %s", err, exc_info=True)
            return "pairing_failed", str(err)
        return None, ""

    async def async_step_pair(self, user_input: dict[str, Any] | None = None) -> Any:
        """Enter the 8-digit HomeKit code and pair.

        Mysa's code is printed on the device, so one submit both opens the
        pair-setup session (if needed) and finishes it.
        """
        code: str | None = None
        if user_input is not None:
            try:
                code = format_pin(user_input[CONF_PAIRING_CODE])
            except aiohomekit.exceptions.MalformedPinError:
                return self._show_pair_form({CONF_PAIRING_CODE: "invalid_code"})

        if self._finish_pairing is None:
            error, detail = await self._async_start_pairing()
            if error is not None:
                return self._show_pair_form({"base": error}, detail)

        if code is None or self._finish_pairing is None:
            return self._show_pair_form()

        errors: dict[str, str] = {}
        detail = ""
        self._pairing_in_progress = True
        try:
            pairing: AbstractPairing = await self._finish_pairing(code)
            await pairing.list_accessories_and_characteristics()
            data = dict(pairing.pairing_data)  # type: ignore[attr-defined]
            info = (
                parse_accessories(pairing.accessories)[0]
                if pairing.accessories is not None
                else AccessoryInfo()
            )
            await pairing.close()
        except AuthenticationError:
            errors[CONF_PAIRING_CODE] = "wrong_code"
        except aiohomekit.exceptions.MaxPeersError:
            errors["base"] = "max_peers"
        except (
            aiohomekit.AccessoryNotFoundError,
            aiohomekit.AccessoryDisconnectedError,
        ):
            return self.async_abort(reason="cannot_connect")
        except HomeKitException as err:
            LOGGER.debug("Pairing failed: %s", err, exc_info=True)
            errors["base"] = "pairing_failed"
            detail = str(err)
        else:
            return await self._async_thermostat_ready(info, data)
        finally:
            self._pairing_in_progress = False
        # A failed attempt ends the session; the next submit opens a new one.
        self._finish_pairing = None
        return self._show_pair_form(errors, detail)

    async def async_step_takeover(
        self, user_input: dict[str, Any] | None = None
    ) -> Any:
        """Pick the HomeKit Device entry to move over."""
        candidates = homekit_device_candidates(self.hass)
        if not candidates:
            return self.async_abort(reason="no_homekit_device_entries")
        if user_input is not None:
            self._takeover_entry_id = user_input[CONF_ENTRY]
            return await self.async_step_takeover_confirm()
        if len(candidates) == 1:
            self._takeover_entry_id = next(iter(candidates))
            return await self.async_step_takeover_confirm()
        return self.async_show_form(
            step_id="takeover",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_ENTRY): SelectSelector(
                        SelectSelectorConfig(
                            options=[
                                SelectOptionDict(value=k, label=v)
                                for k, v in candidates.items()
                            ]
                        )
                    )
                }
            ),
        )

    async def async_step_takeover_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> Any:
        """Add our own pairing through the existing one, then hand over."""
        entry = (
            self.hass.config_entries.async_get_entry(self._takeover_entry_id)
            if self._takeover_entry_id
            else None
        )
        if entry is None or entry.domain != HKC_DOMAIN:
            return self.async_abort(reason="no_homekit_device_entries")
        errors: dict[str, str] = {}
        detail = ""
        if user_input is not None:
            try:
                pairing, info = await async_copy_pairing(self.hass, entry.data)
            except aiohomekit.exceptions.MaxPeersError:
                errors["base"] = "max_peers"
            except AuthenticationError as err:
                errors["base"] = "takeover_failed"
                detail = str(err)
            except CONNECTION_ERRORS:
                errors["base"] = "cannot_connect_device"
            except HomeKitException as err:
                LOGGER.debug("Takeover failed: %s", err, exc_info=True)
                errors["base"] = "takeover_failed"
                detail = str(err)
            else:
                return await self._async_thermostat_ready(info, pairing, entry.entry_id)
        return self.async_show_form(
            step_id="takeover_confirm",
            data_schema=vol.Schema({}),
            errors=errors,
            description_placeholders={"name": entry.title, "error": detail},
        )

    def _untested_form(self, step_id: str, model: str | None) -> Any:
        return self.async_show_form(
            step_id=step_id,
            data_schema=vol.Schema({}),
            description_placeholders={
                "model": MODEL_NAMES.get(model or "", model or "unknown")
                + (f" ({model})" if model else ""),
            },
        )


class MysaConfigFlow(ThermostatSteps, ConfigFlow, domain=DOMAIN):
    """Set up Mysa Local."""

    VERSION = 1

    def __init__(self) -> None:
        """Start empty."""
        self._discoveries = {}
        self._pending: list[ConfigSubentryData] = []
        self._cloud: tuple[str, str, str | None, Bootstrap] | None = None

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Hub options."""
        return MysaOptionsFlow()

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        """Thermostats are subentries."""
        return {SUBENTRY_THERMOSTAT: ThermostatSubentryFlow}

    @override
    def is_matching(self, other_flow: MysaConfigFlow) -> bool:
        """Treat discoveries of the same thermostat as one flow."""
        return (
            self._hkid is not None
            and other_flow._hkid == self._hkid
            and not other_flow._pairing_in_progress
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose how to add the first thermostat."""
        if hub_entry(self.hass) is not None:
            return self.async_abort(reason="already_configured_hub")
        menu = ["pick"]
        if homekit_device_candidates(self.hass):
            menu.append("takeover")
        menu += ["cloud_login", "wifi_help"]
        return self.async_show_menu(step_id="user", menu_options=menu)

    async def async_step_homekit(
        self, discovery_info: ZeroconfServiceInfo
    ) -> ConfigFlowResult:
        """A Mysa advertised itself over HomeKit."""
        props = {k.lower(): v for k, v in discovery_info.properties.items()}
        if "id" not in props or "md" not in props:
            return self.async_abort(reason="invalid_discovery")
        hkid = str(props["id"]).lower()
        model = str(props["md"])
        if not is_mysa_model(model):
            return self.async_abort(reason="not_supported")
        try:
            paired = not int(props.get("sf", 1)) & 0x01
        except ValueError:
            paired = False

        hub = hub_entry(self.hass)
        if hub is not None and (subentry_id := configured_hkids(hub).get(hkid)):
            subentry = hub.subentries[subentry_id]
            pairing = dict(subentry.data.get(CONF_PAIRING) or {})
            endpoint = {
                "AccessoryIP": discovery_info.host,
                "AccessoryIPs": [
                    str(ip)
                    for ip in discovery_info.ip_addresses
                    if not ip.is_link_local and not ip.is_unspecified
                ],
                "AccessoryPort": discovery_info.port,
            }
            if any(pairing.get(k) != endpoint[k] for k in ENDPOINT_KEYS):
                pairing.update(endpoint)
                self.hass.config_entries.async_update_subentry(
                    hub, subentry, data={**subentry.data, CONF_PAIRING: pairing}
                )
            return self.async_abort(reason="already_configured")

        await self.async_set_unique_id(f"hk_{hkid}")
        self._abort_if_unique_id_configured()
        self._hkid = hkid
        self._model = model
        self._name = domain_to_name(discovery_info.name)
        self.context["title_placeholders"] = {"name": self._name}
        if self.hass.config_entries.flow.async_has_matching_flow(self):
            return self.async_abort(reason="already_in_progress")

        if paired:
            for entry_id in homekit_device_candidates(self.hass):
                entry = self.hass.config_entries.async_get_entry(entry_id)
                if (
                    entry
                    and str(entry.data.get("AccessoryPairingID", "")).lower() == hkid
                ):
                    self._takeover_entry_id = entry_id
                    return await self.async_step_takeover_confirm()
            return self.async_abort(reason="already_paired")
        return self._show_pair_form()

    async def _async_thermostat_ready(
        self,
        info: AccessoryInfo,
        pairing: Mapping[str, Any],
        migrate_from: str | None = None,
    ) -> ConfigFlowResult:
        hub = hub_entry(self.hass)
        title = info.name or self._name or "Mysa"
        if hub is not None:
            return self._async_add_to_hub(hub, info, pairing, migrate_from)
        self._pending = [thermostat_subentry(info, pairing, title, migrate_from)]
        return await self.async_step_cloud_offer()

    @callback
    def _async_add_to_hub(
        self,
        hub: ConfigEntry,
        info: AccessoryInfo,
        pairing: Mapping[str, Any],
        migrate_from: str | None,
    ) -> ConfigFlowResult:
        """Add (or upgrade) a thermostat subentry on the existing entry."""
        runtime = getattr(hub, "runtime_data", None)
        record = None
        if runtime is not None and runtime.cloud is not None and info.device_id:
            record = runtime.cloud.record(info.device_id)
        title = (record.name if record else None) or info.name or self._name or "Mysa"
        new = thermostat_subentry(info, pairing, title, migrate_from)
        for subentry in hub.subentries.values():
            if subentry.unique_id == new["unique_id"]:
                self.hass.config_entries.async_update_subentry(
                    hub, subentry, data={**subentry.data, **new["data"]}
                )
                return self.async_abort(reason="thermostat_updated")
        self.hass.config_entries.async_add_subentry(
            hub,
            ConfigSubentry(
                data=new["data"],  # type: ignore[arg-type]
                subentry_type=new["subentry_type"],
                title=new["title"],
                unique_id=new["unique_id"],
            ),
        )
        return self.async_abort(
            reason="thermostat_added", description_placeholders={"name": title}
        )

    async def async_step_cloud_offer(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Offer the optional Mysa sign-in."""
        return self.async_show_menu(
            step_id="cloud_offer", menu_options=["cloud_login", "finish"]
        )

    async def async_step_cloud_login(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Sign in to Mysa (optional)."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                token, user_id, bootstrap = await async_sign_in(
                    self.hass, user_input[CONF_EMAIL], user_input[CONF_PASSWORD]
                )
            except MysaError as err:
                errors["base"] = sign_in_error(err)
            else:
                self._cloud = (user_input[CONF_EMAIL], token, user_id, bootstrap)
                # Name paired thermostats after their Mysa name.
                for item in self._pending:
                    record = bootstrap.devices.get(item["unique_id"] or "")
                    if record and record.name:
                        item["title"] = record.name
                if self._pending:
                    return await self.async_step_finish()
                return await self.async_step_cloud_devices()
        return self.async_show_form(
            step_id="cloud_login",
            data_schema=self.add_suggested_values_to_schema(
                CREDENTIALS_SCHEMA, user_input
            ),
            errors=errors,
        )

    async def async_step_cloud_devices(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick thermostats from the account (no local path)."""
        assert self._cloud is not None
        devices = self._cloud[3].devices
        if not devices:
            return await self.async_step_finish()
        if user_input is not None:
            for device_id in user_input.get(CONF_DEVICES, []):
                record = devices[device_id]
                self._pending.append(
                    thermostat_subentry(
                        AccessoryInfo(),
                        None,
                        record.name or device_id,
                        device_id=device_id,
                        model=record.model,
                    )
                )
            return await self.async_step_finish()
        options = [
            SelectOptionDict(
                value=dev,
                label=f"{rec.name or dev} ({MODEL_NAMES.get(rec.model or '', rec.model or '?')})",
            )
            for dev, rec in devices.items()
        ]
        return self.async_show_form(
            step_id="cloud_devices",
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_DEVICES, default=list(devices)): SelectSelector(
                        SelectSelectorConfig(options=options, multiple=True)
                    )
                }
            ),
        )

    async def async_step_finish(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show the untested-model notice if needed, then create the entry."""
        models = [item["data"].get(CONF_MODEL) for item in self._pending]
        untested = next((m for m in models if m not in TESTED_MODELS), None)
        if untested is not None and user_input is None:
            return self._untested_form("finish", untested)
        await self.async_set_unique_id(DOMAIN, raise_on_progress=False)
        self._abort_if_unique_id_configured()
        data: dict[str, Any] = {}
        if self._cloud is not None:
            email, token, user_id, _ = self._cloud
            data = {CONF_EMAIL: email, CONF_REFRESH_TOKEN: token, CONF_USER_ID: user_id}
        return self.async_create_entry(
            title="Mysa",
            data=data,
            options={
                CONF_CLOUD_ENABLED: True,
                CONF_SCAN_INTERVAL: DEFAULT_SCAN_INTERVAL,
            },
            subentries=self._pending,
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """The stored sign-in stopped working."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the Mysa password again."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                token, user_id, _ = await async_sign_in(
                    self.hass, user_input[CONF_EMAIL], user_input[CONF_PASSWORD]
                )
            except MysaError as err:
                errors["base"] = sign_in_error(err)
            else:
                stored = entry.data.get(CONF_USER_ID)
                if stored and user_id and stored != user_id:
                    return self.async_abort(reason="wrong_account")
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates={
                        CONF_EMAIL: user_input[CONF_EMAIL],
                        CONF_REFRESH_TOKEN: token,
                        CONF_USER_ID: user_id,
                    },
                )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=self.add_suggested_values_to_schema(
                CREDENTIALS_SCHEMA, {CONF_EMAIL: entry.data.get(CONF_EMAIL, "")}
            ),
            errors=errors,
        )


class ThermostatSubentryFlow(ThermostatSteps, ConfigSubentryFlow):
    """Add or adjust one thermostat."""

    def __init__(self) -> None:
        """Start empty."""
        self._discoveries = {}
        self._pending_sub: ConfigSubentryData | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Choose how to add the thermostat."""
        menu = ["pick"]
        if homekit_device_candidates(self.hass):
            menu.append("takeover")
        if self._cloud_choices():
            menu.append("cloud_pick")
        menu.append("wifi_help")
        return self.async_show_menu(step_id="user", menu_options=menu)

    def _cloud_choices(self) -> dict[str, str]:
        entry = self._get_entry()
        runtime = getattr(entry, "runtime_data", None)
        if runtime is None or runtime.cloud is None or runtime.cloud.data is None:
            return {}
        known = {s.unique_id for s in entry.subentries.values()}
        return {
            dev: f"{rec.name or dev} ({MODEL_NAMES.get(rec.model or '', rec.model or '?')})"
            for dev, rec in runtime.cloud.data.records.items()
            if dev not in known
        }

    async def async_step_cloud_pick(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Add a thermostat from the Mysa account (cloud control only)."""
        choices = self._cloud_choices()
        if not choices:
            return self.async_abort(reason="no_cloud_devices")
        if user_input is not None:
            entry = self._get_entry()
            record = entry.runtime_data.cloud.record(user_input[CONF_DEVICE])
            self._pending_sub = thermostat_subentry(
                AccessoryInfo(),
                None,
                (record.name if record else None) or user_input[CONF_DEVICE],
                device_id=user_input[CONF_DEVICE],
                model=record.model if record else None,
            )
            return await self.async_step_confirm()
        return self.async_show_form(
            step_id="cloud_pick",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_DEVICE): SelectSelector(
                        SelectSelectorConfig(
                            options=[
                                SelectOptionDict(value=k, label=v)
                                for k, v in choices.items()
                            ]
                        )
                    )
                }
            ),
        )

    async def _async_thermostat_ready(
        self,
        info: AccessoryInfo,
        pairing: Mapping[str, Any],
        migrate_from: str | None = None,
    ) -> SubentryFlowResult:
        entry = self._get_entry()
        runtime = getattr(entry, "runtime_data", None)
        record = None
        if runtime is not None and runtime.cloud is not None and info.device_id:
            record = runtime.cloud.record(info.device_id)
        title = (record.name if record else None) or info.name or self._name or "Mysa"
        new = thermostat_subentry(info, pairing, title, migrate_from)
        for subentry in entry.subentries.values():
            if subentry.unique_id == new["unique_id"]:
                self.hass.config_entries.async_update_subentry(
                    entry, subentry, data={**subentry.data, **new["data"]}
                )
                return self.async_abort(reason="thermostat_updated")
        self._pending_sub = new
        return await self.async_step_confirm()

    async def async_step_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Untested-model notice, then create."""
        assert self._pending_sub is not None
        model = self._pending_sub["data"].get(CONF_MODEL)
        if model not in TESTED_MODELS and user_input is None:
            return self._untested_form("confirm", model)
        return self.async_create_entry(
            title=self._pending_sub["title"],
            data=self._pending_sub["data"],
            unique_id=self._pending_sub["unique_id"],
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Heater wattage and runtime source."""
        subentry = self._get_reconfigure_subentry()
        if user_input is not None:
            return self.async_update_and_abort(
                self._get_entry(),
                subentry,
                data_updates={
                    CONF_HEATER_WATTS: float(user_input.get(CONF_HEATER_WATTS) or 0),
                    CONF_CLOUD_RUNTIME: bool(user_input[CONF_CLOUD_RUNTIME]),
                },
            )
        schema = vol.Schema(
            {
                vol.Optional(CONF_HEATER_WATTS): NumberSelector(
                    NumberSelectorConfig(
                        min=0,
                        max=10000,
                        step=50,
                        mode=NumberSelectorMode.BOX,
                        unit_of_measurement="W",
                    )
                ),
                vol.Required(CONF_CLOUD_RUNTIME): BooleanSelector(),
            }
        )
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(
                schema,
                {
                    CONF_HEATER_WATTS: subentry.data.get(CONF_HEATER_WATTS, 0),
                    CONF_CLOUD_RUNTIME: subentry.data.get(CONF_CLOUD_RUNTIME, True),
                },
            ),
            description_placeholders={"name": subentry.title},
        )


class MysaOptionsFlow(OptionsFlow):
    """Cloud on/off, polling, firmware install; sign in or out."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose what to change."""
        signed_in = bool(self.config_entry.data.get(CONF_REFRESH_TOKEN))
        return self.async_show_menu(
            step_id="init",
            menu_options=["settings", "sign_out" if signed_in else "sign_in"],
        )

    async def async_step_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Cloud and polling settings."""
        options = self.config_entry.options
        if user_input is not None:
            advanced = user_input.pop("advanced", {})
            user_input[CONF_SCAN_INTERVAL] = int(user_input[CONF_SCAN_INTERVAL])
            user_input[CONF_FIRMWARE_INSTALL] = bool(
                advanced.get(CONF_FIRMWARE_INSTALL)
            )
            return self.async_create_entry(data={**options, **user_input})
        fields: dict[Any, Any] = {}
        if self.config_entry.data.get(CONF_REFRESH_TOKEN):
            fields[vol.Required(CONF_CLOUD_ENABLED)] = BooleanSelector()
        fields[vol.Required(CONF_SCAN_INTERVAL)] = NumberSelector(
            NumberSelectorConfig(
                min=MIN_SCAN_INTERVAL,
                max=MAX_SCAN_INTERVAL,
                step=10,
                mode=NumberSelectorMode.SLIDER,
                unit_of_measurement="s",
            )
        )
        fields[vol.Required("advanced")] = section(
            vol.Schema({vol.Required(CONF_FIRMWARE_INSTALL): BooleanSelector()}),
            {"collapsed": True},
        )
        return self.async_show_form(
            step_id="settings",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema(fields),
                {
                    CONF_CLOUD_ENABLED: options.get(CONF_CLOUD_ENABLED, True),
                    CONF_SCAN_INTERVAL: options.get(
                        CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
                    ),
                    "advanced": {
                        CONF_FIRMWARE_INSTALL: options.get(CONF_FIRMWARE_INSTALL, False)
                    },
                },
            ),
        )

    async def async_step_sign_in(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Add the Mysa cloud to a local-only setup."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                token, user_id, _ = await async_sign_in(
                    self.hass, user_input[CONF_EMAIL], user_input[CONF_PASSWORD]
                )
            except MysaError as err:
                errors["base"] = sign_in_error(err)
            else:
                self.hass.config_entries.async_update_entry(
                    self.config_entry,
                    data={
                        **self.config_entry.data,
                        CONF_EMAIL: user_input[CONF_EMAIL],
                        CONF_REFRESH_TOKEN: token,
                        CONF_USER_ID: user_id,
                    },
                )
                return self.async_create_entry(
                    data={**self.config_entry.options, CONF_CLOUD_ENABLED: True}
                )
        return self.async_show_form(
            step_id="sign_in",
            data_schema=self.add_suggested_values_to_schema(
                CREDENTIALS_SCHEMA, user_input
            ),
            errors=errors,
        )

    async def async_step_sign_out(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Forget the stored Mysa sign-in."""
        if user_input is not None:
            data = {
                k: v
                for k, v in self.config_entry.data.items()
                if k not in (CONF_REFRESH_TOKEN, CONF_USER_ID, CONF_EMAIL)
            }
            self.hass.config_entries.async_update_entry(self.config_entry, data=data)
            return self.async_create_entry(data=dict(self.config_entry.options))
        return self.async_show_form(step_id="sign_out", data_schema=vol.Schema({}))
