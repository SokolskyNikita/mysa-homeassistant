"""Initial Mysa setup and HomeKit discovery; register the other flow handlers."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, override

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlowResult,
    ConfigSubentry,
    ConfigSubentryData,
    ConfigSubentryFlow,
    OptionsFlow,
)
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD, CONF_SCAN_INTERVAL
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
)
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo
import voluptuous as vol

from .account import (
    ACCOUNT_OFF,
    account_client,
    account_status,
    can_claim,
    can_pair_locally,
    is_supported_model,
)
from .const import (
    CONF_CLOUD_ENABLED,
    CONF_LIVE_MODE,
    CONF_MODEL,
    CONF_PAIRING,
    CONF_SAVE_PASSWORD,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    LOGGER,
    SUBENTRY_THERMOSTAT,
    USER_AGENT,
    is_mysa_model,
)
from .flows.account import (
    SIGN_IN_SCHEMA,
    MysaReauthFlow,
    async_sign_in,
    sign_in_data,
    sign_in_error,
)
from .flows.options import MysaOptionsFlow
from .flows.shared import (
    CONF_DEVICES,
    configured_hkids,
    homekit_device_candidates,
    hub_entry,
    thermostat_subentry,
)
from .flows.subentry import ThermostatSubentryFlow
from .flows.thermostat import ThermostatSteps
from .homekit_adapter import (
    discovery_from_zeroconf,
    update_pairing_endpoints,
)
from .pairing_store import pairing_store
from .protocols.cloud import (
    MODEL_NAMES,
    TESTED_MODELS,
    Bootstrap,
    MysaAuth,
    MysaClient,
    MysaError,
)
from .protocols.homekit import (
    AccessoryInfo,
    HomeKitError,
)


class MysaConfigFlow(ThermostatSteps, MysaReauthFlow, domain=DOMAIN):
    """Set up Mysa Local."""

    VERSION = 1

    def __init__(self) -> None:
        """Start empty."""
        super().__init__()
        self._pending: list[ConfigSubentryData] = []
        self._cloud: tuple[str, str, str | None, Bootstrap] | None = None
        # Entry data from the sign-in (email, token, user ID, saved password).
        self._cloud_data: dict[str, Any] = {}
        self._cloud_client: MysaClient | None = None
        # Thermostats still to offer for the account / for local pairing.
        self._account_queue: list[ConfigSubentryData] = []
        self._local_queue: list[str] = []
        # A thermostat waiting to be added to the existing entry.
        self._hub_pending: (
            tuple[AccessoryInfo, Mapping[str, Any], str | None] | None
        ) = None

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
            self._thermostat.hkid is not None
            and other_flow._thermostat.hkid == self._thermostat.hkid
            and not other_flow._thermostat.pairing_in_progress
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
        try:
            advertisement = discovery_from_zeroconf(discovery_info)
        except HomeKitError as err:
            LOGGER.debug("Ignoring invalid HomeKit discovery data: %s", err)
            return self.async_abort(reason="invalid_discovery")
        if advertisement is None:
            return self.async_abort(reason="invalid_discovery")
        if not is_mysa_model(advertisement.model):
            return self.async_abort(reason="not_supported")
        hkid = advertisement.id

        hub = hub_entry(self.hass)
        if hub is not None and (subentry_id := configured_hkids(hub).get(hkid)):
            subentry = hub.subentries[subentry_id]
            pairing = dict(subentry.data.get(CONF_PAIRING) or {})
            updated_pairing = update_pairing_endpoints(pairing, advertisement)
            if pairing != updated_pairing:
                self.hass.config_entries.async_update_subentry(
                    hub,
                    subentry,
                    data={**subentry.data, CONF_PAIRING: updated_pairing},
                )
            return self.async_abort(reason="already_configured")

        await self.async_set_unique_id(f"hk_{hkid}")
        self._abort_if_unique_id_configured()
        self._thermostat.hkid = hkid
        self._thermostat.model = advertisement.model
        self._thermostat.name = advertisement.name
        self.context["title_placeholders"] = {"name": self._thermostat.name}
        if self.hass.config_entries.flow.async_has_matching_flow(self):
            return self.async_abort(reason="already_in_progress")

        store = pairing_store(self.hass)
        if pending := (await store.async_load()).get(hkid):
            if advertisement.paired:
                return await self._async_resume_pending_pairing(hkid, pending)
            await store.async_clear(hkid)

        if advertisement.paired:
            for entry_id in homekit_device_candidates(self.hass):
                entry = self.hass.config_entries.async_get_entry(entry_id)
                if (
                    entry
                    and str(entry.data.get("AccessoryPairingID", "")).lower() == hkid
                ):
                    self._thermostat.takeover_entry_id = entry_id
                    return await self.async_step_takeover_confirm()
            return self.async_abort(reason="already_paired")
        return self._show_pair_form()

    def _account_client(self) -> MysaClient | None:
        if self._cloud is None:
            return super()._account_client()
        if self._cloud_client is None:
            session = async_get_clientsession(self.hass)
            auth = MysaAuth(session, self._cloud[1], user_agent=USER_AGENT)
            self._cloud_client = MysaClient(session, auth, user_agent=USER_AGENT)
        return self._cloud_client

    async def _async_thermostat_ready(
        self,
        info: AccessoryInfo,
        pairing: Mapping[str, Any],
        migrate_from: str | None = None,
    ) -> ConfigFlowResult:
        if (wrong := await self._async_wrong_thermostat(info, pairing)) is not None:
            return wrong
        if self._thermostat.hkid is not None:
            await self._async_store_pairing_backup(
                self._thermostat.hkid,
                pairing,
                name=info.name or self._thermostat.name,
                model=info.model or self._thermostat.model,
                migrate_from=migrate_from,
            )
        hub = hub_entry(self.hass)
        title = info.name or self._thermostat.name or "Mysa"
        if hub is not None:
            device_id = info.device_id or ""
            if (
                not any(s.unique_id == device_id for s in hub.subentries.values())
                and account_status(hub, device_id) == ACCOUNT_OFF
                and can_claim(device_id, info.model)
                and account_client(hub) is not None
            ):
                self._hub_pending = (info, pairing, migrate_from)
                self._thermostat.account_device = (device_id, info.model, title)
                self._thermostat.account_optional = True
                return await self.async_step_account()
            return await self._async_add_to_hub(hub, info, pairing, migrate_from)
        if self._thermostat.target_device_id is not None:
            # A thermostat picked from the account, now paired locally too.
            for item in self._pending:
                if item["unique_id"] == self._thermostat.target_device_id:
                    item["data"] = {**item["data"], CONF_PAIRING: dict(pairing)}
            self._thermostat.target_device_id = None
            return await self._async_next_local()
        self._pending = [thermostat_subentry(info, pairing, title, migrate_from)]
        return await self.async_step_cloud_offer()

    async def _async_account_done(self) -> ConfigFlowResult:
        if self._hub_pending is not None:
            info, pairing, migrate_from = self._hub_pending
            self._hub_pending = None
            hub = hub_entry(self.hass)
            if hub is None:  # pragma: no cover - removed mid-flow
                return self.async_abort(reason="already_configured")
            return await self._async_add_to_hub(hub, info, pairing, migrate_from)
        return await self._async_next_account()

    async def _async_next_account(self) -> ConfigFlowResult:
        """Offer the next paired thermostat that isn't on the account yet."""
        if self._account_queue:
            item = self._account_queue.pop(0)
            self._thermostat.account_device = (
                item["unique_id"] or "",
                item["data"].get(CONF_MODEL),
                item["title"],
            )
            self._thermostat.account_optional = True
            return await self.async_step_account()
        return await self.async_step_finish()

    async def _async_next_local(self) -> ConfigFlowResult:
        """Offer local pairing for the next thermostat picked from the account."""
        while self._local_queue:
            device_id = self._local_queue.pop(0)
            item = next((i for i in self._pending if i["unique_id"] == device_id), None)
            if item is None or item["data"].get(CONF_PAIRING):
                continue
            self._thermostat.target_device_id = device_id
            self._thermostat.target_name = item["title"]
            return await self.async_step_local_offer()
        return await self.async_step_finish()

    async def _async_local_skipped(self) -> ConfigFlowResult:
        return await self._async_next_local()

    async def _async_add_to_hub(
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
        title = (
            (record.name if record else None)
            or info.name
            or self._thermostat.name
            or "Mysa"
        )
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
                self._cloud_data = sign_in_data(
                    user_input,
                    token,
                    user_id,
                    save=bool(user_input.get(CONF_SAVE_PASSWORD)),
                )
                self._thermostat.homes = bootstrap.homes or None
                # Name paired thermostats after their Mysa name.
                for item in self._pending:
                    record = bootstrap.devices.get(item["unique_id"] or "")
                    if record and record.name:
                        item["title"] = record.name
                if self._pending:
                    # Offer the account for paired thermostats that aren't on it.
                    self._account_queue = [
                        item
                        for item in self._pending
                        if can_claim(
                            item["unique_id"] or "", item["data"].get(CONF_MODEL)
                        )
                        and item["unique_id"] not in bootstrap.devices
                    ]
                    return await self._async_next_account()
                return await self.async_step_cloud_devices()
        return self.async_show_form(
            step_id="cloud_login",
            data_schema=self.add_suggested_values_to_schema(SIGN_IN_SCHEMA, user_input),
            errors=errors,
        )

    async def async_step_cloud_devices(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick thermostats from the account (no local path)."""
        assert self._cloud is not None
        devices = {
            dev: rec
            for dev, rec in self._cloud[3].devices.items()
            if is_supported_model(rec.model)
        }
        if not devices:
            return await self.async_step_finish()
        if user_input is not None:
            selected = user_input.get(CONF_DEVICES, [])
            for device_id in selected:
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
            # Offer to pair each one locally too (models with HomeKit).
            self._local_queue = [
                device_id
                for device_id in selected
                if can_pair_locally(devices[device_id].model)
            ]
            return await self._async_next_local()
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
        data = dict(self._cloud_data) if self._cloud is not None else {}
        return self.async_create_entry(
            title="Mysa",
            data=data,
            options={
                CONF_CLOUD_ENABLED: True,
                CONF_SCAN_INTERVAL: DEFAULT_SCAN_INTERVAL,
                CONF_LIVE_MODE: False,
            },
            subentries=self._pending,
        )
