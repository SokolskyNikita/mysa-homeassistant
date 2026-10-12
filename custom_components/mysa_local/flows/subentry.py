"""Thermostat subentry setup and account management."""

from collections.abc import Mapping
from typing import Any

from homeassistant.config_entries import (
    SOURCE_RECONFIGURE,
    ConfigSubentryData,
    ConfigSubentryFlow,
    SubentryFlowResult,
)
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.selector import (
    BooleanSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
)
from mysa_client.cloud import (
    MODEL_NAMES,
    TESTED_MODELS,
    MysaError,
)
from mysa_client.cloud.const import (
    EXPERIMENTAL_CLOUD_FAMILIES,
    model_family,
)
from mysa_client.homekit import AccessoryInfo
import voluptuous as vol

from custom_components.mysa_local.account import (
    ACCOUNT_OFF,
    ACCOUNT_ON,
    account_client,
    account_status,
    async_refresh_account,
    async_remove_from_account,
    can_claim,
    can_import_cloud,
    can_pair_locally,
)
from custom_components.mysa_local.const import (
    CONF_CLOUD_RUNTIME,
    CONF_DEVICE_ID,
    CONF_HEATER_WATTS,
    CONF_MODEL,
    CONF_MQTT_ENABLED,
    CONF_PAIRING,
)

from .shared import (
    CONF_DEVICE,
    LOCAL_TEXT,
    account_line,
    homekit_device_candidates,
    local_line,
    names_differ,
    thermostat_subentry,
)
from .thermostat import ThermostatSteps


class ThermostatSubentryFlow(ThermostatSteps, ConfigSubentryFlow):
    """Add or adjust one thermostat."""

    def __init__(self) -> None:
        """Start empty."""
        super().__init__()
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
            if dev not in known and can_import_cloud(rec.model)
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
            device_id = user_input[CONF_DEVICE]
            record = entry.runtime_data.cloud.record(device_id)
            model = record.model if record else None
            self._pending_sub = thermostat_subentry(
                AccessoryInfo(),
                None,
                (record.name if record else None) or device_id,
                device_id=device_id,
                model=model,
            )
            self._pending_sub["data"] = {
                **self._pending_sub["data"],
                CONF_MQTT_ENABLED: True,
            }
            if can_pair_locally(model):
                self._thermostat.target_device_id = device_id
                self._thermostat.target_name = self._pending_sub["title"]
                return await self.async_step_local_offer()
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
        if (wrong := await self._async_wrong_thermostat(info, pairing)) is not None:
            return wrong
        entry = self._get_entry()
        runtime = getattr(entry, "runtime_data", None)
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
        new["data"] = {**new["data"], CONF_MQTT_ENABLED: True}
        for subentry in entry.subentries.values():
            if subentry.unique_id == new["unique_id"]:
                updated_data = {**subentry.data, **new["data"]}
                # Re-identifying a thermostat must not undo its saved per-device
                # MQTT choice. Old entries without this key retain the default.
                updated_data[CONF_MQTT_ENABLED] = subentry.data.get(
                    CONF_MQTT_ENABLED, True
                )
                self.hass.config_entries.async_update_subentry(
                    entry, subentry, data=updated_data
                )
                return self.async_abort(reason="thermostat_updated")
        self._pending_sub = new
        device_id = new["unique_id"] or ""
        model = new["data"].get(CONF_MODEL)
        if (
            account_status(entry, device_id) == ACCOUNT_OFF
            and can_claim(device_id, model)
            and account_client(entry) is not None
        ):
            self._thermostat.account_device = (device_id, model, title)
            self._thermostat.account_optional = True
            return await self.async_step_account()
        return await self.async_step_confirm()

    async def _async_account_done(self) -> SubentryFlowResult:
        if self.source == SOURCE_RECONFIGURE:
            await async_refresh_account(self._get_entry())
            return self.async_abort(reason="added_to_account")
        return await self.async_step_confirm()

    async def _async_local_skipped(self) -> SubentryFlowResult:
        if self.source == SOURCE_RECONFIGURE:
            return self.async_abort(reason="local_skipped")
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
        """The thermostat's status, settings, and its cloud and local setup."""
        subentry = self._get_reconfigure_subentry()
        entry = self._get_entry()
        device_id = subentry.data[CONF_DEVICE_ID]
        model = subentry.data.get(CONF_MODEL)
        paired = bool(subentry.data.get(CONF_PAIRING))
        status = account_status(entry, device_id)
        has_client = account_client(entry) is not None
        cloud_name = self._cloud_name()
        basic = model_family(model) in EXPERIMENTAL_CLOUD_FAMILIES
        menu = ["cloud_info", "mqtt"] if basic else ["settings"]
        if not paired and can_pair_locally(model):
            menu.append("local_pair")
        if status == ACCOUNT_OFF and has_client and can_claim(device_id, model):
            menu.append("account_add")
        if status == ACCOUNT_ON and names_differ(subentry.title, cloud_name):
            menu.append("name_from_mysa")
            if has_client and not basic:
                menu.append("name_to_mysa")
        if status == ACCOUNT_ON and has_client and not basic:
            menu.append("account_remove")
        return self.async_show_menu(
            step_id="reconfigure",
            menu_options=menu,
            description_placeholders={
                "name": subentry.title,
                "account": account_line(status, subentry.title, cloud_name),
                "local": local_line(paired)
                if not basic
                else "Cloud only · experimental support. Manage device setup in the Mysa app.",
            },
        )

    async def async_step_cloud_info(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Explain the experimental cloud-only scope without an empty settings menu."""
        if user_input is not None:
            return self.async_abort(reason="cloud_info")
        return self.async_show_form(step_id="cloud_info", data_schema=vol.Schema({}))

    async def async_step_mqtt(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Enable or disable MQTT for this thermostat alone."""
        subentry = self._get_reconfigure_subentry()
        current = subentry.data.get(CONF_MQTT_ENABLED, True)
        if user_input is not None:
            return self.async_update_and_abort(
                self._get_entry(),
                subentry,
                data_updates={
                    CONF_MQTT_ENABLED: bool(user_input.get(CONF_MQTT_ENABLED, current))
                },
            )
        schema = vol.Schema({vol.Optional(CONF_MQTT_ENABLED): BooleanSelector()})
        return self.async_show_form(
            step_id="mqtt",
            data_schema=self.add_suggested_values_to_schema(
                schema, {CONF_MQTT_ENABLED: current}
            ),
            description_placeholders={"name": subentry.title},
        )

    def _cloud_name(self) -> str | None:
        """The thermostat's name in the Mysa app, if known."""
        entry = self._get_entry()
        device_id = self._get_reconfigure_subentry().data[CONF_DEVICE_ID]
        runtime = getattr(entry, "runtime_data", None)
        cloud = runtime.cloud if runtime else None
        record = cloud.record(device_id) if cloud is not None else None
        return record.name if record else None

    async def async_step_name_from_mysa(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Use the Mysa app's name for the thermostat in Home Assistant."""
        subentry = self._get_reconfigure_subentry()
        cloud_name = self._cloud_name()
        if not cloud_name:
            return self.async_abort(reason="cloud_unavailable")
        if user_input is not None:
            # The entry reloads; the device and its entities take the new name.
            self.hass.config_entries.async_update_subentry(
                self._get_entry(), subentry, title=cloud_name
            )
            return self.async_abort(
                reason="renamed_here", description_placeholders={"name": cloud_name}
            )
        return self.async_show_form(
            step_id="name_from_mysa",
            data_schema=vol.Schema({}),
            description_placeholders={"name": subentry.title, "cloud_name": cloud_name},
        )

    async def async_step_name_to_mysa(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Rename the thermostat in the Mysa app to its Home Assistant name."""
        subentry = self._get_reconfigure_subentry()
        entry = self._get_entry()
        cloud_name = self._cloud_name()
        runtime = getattr(entry, "runtime_data", None)
        cloud = runtime.cloud if runtime else None
        if cloud is None or not cloud_name:
            return self.async_abort(reason="cloud_unavailable")
        errors: dict[str, str] = {}
        detail = ""
        if user_input is not None:
            try:
                await cloud.async_update_settings(
                    subentry.data[CONF_DEVICE_ID], {"Name": subentry.title}
                )
            except HomeAssistantError as err:
                errors["base"] = "cannot_connect"
                detail = str(err)
            else:
                return self.async_abort(
                    reason="renamed_in_mysa",
                    description_placeholders={"name": subentry.title},
                )
        return self.async_show_form(
            step_id="name_to_mysa",
            data_schema=vol.Schema({}),
            errors=errors,
            description_placeholders={
                "name": subentry.title,
                "cloud_name": cloud_name,
                "error": detail,
            },
        )

    async def async_step_account_add(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Put this thermostat on the Mysa account."""
        subentry = self._get_reconfigure_subentry()
        self._thermostat.account_device = (
            subentry.data[CONF_DEVICE_ID],
            subentry.data.get(CONF_MODEL),
            subentry.title,
        )
        self._thermostat.account_optional = False
        return await self.async_step_account()

    async def async_step_account_remove(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Take this thermostat off the Mysa account."""
        subentry = self._get_reconfigure_subentry()
        entry = self._get_entry()
        device_id = subentry.data[CONF_DEVICE_ID]
        client = account_client(entry)
        errors: dict[str, str] = {}
        detail = ""
        if user_input is not None and client is not None:
            record = entry.runtime_data.cloud.record(device_id)
            try:
                await async_remove_from_account(
                    client, device_id, record.home_id if record else None
                )
            except MysaError as err:
                errors["base"] = "cannot_connect"
                detail = str(err)
            else:
                await async_refresh_account(entry)
                return self.async_abort(
                    reason="removed_from_account",
                    description_placeholders={"name": subentry.title},
                )
        return self.async_show_form(
            step_id="account_remove",
            data_schema=vol.Schema({}),
            errors=errors,
            description_placeholders={
                "name": subentry.title,
                "local": LOCAL_TEXT[bool(subentry.data.get(CONF_PAIRING))],
                "error": detail,
            },
        )

    async def async_step_local_pair(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Pair this cloud-only thermostat over HomeKit."""
        subentry = self._get_reconfigure_subentry()
        self._thermostat.target_device_id = subentry.data[CONF_DEVICE_ID]
        self._thermostat.target_name = subentry.title
        return await self.async_step_local_find()

    async def async_step_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Heater wattage, runtime source, and per-device MQTT readings."""
        subentry = self._get_reconfigure_subentry()
        if user_input is not None:
            return self.async_update_and_abort(
                self._get_entry(),
                subentry,
                data_updates={
                    CONF_HEATER_WATTS: float(user_input.get(CONF_HEATER_WATTS) or 0),
                    CONF_CLOUD_RUNTIME: bool(user_input[CONF_CLOUD_RUNTIME]),
                    CONF_MQTT_ENABLED: bool(
                        user_input.get(
                            CONF_MQTT_ENABLED,
                            subentry.data.get(CONF_MQTT_ENABLED, True),
                        )
                    ),
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
                vol.Optional(CONF_MQTT_ENABLED): BooleanSelector(),
            }
        )
        return self.async_show_form(
            step_id="settings",
            data_schema=self.add_suggested_values_to_schema(
                schema,
                {
                    CONF_HEATER_WATTS: subentry.data.get(CONF_HEATER_WATTS, 0),
                    CONF_CLOUD_RUNTIME: subentry.data.get(CONF_CLOUD_RUNTIME, True),
                    CONF_MQTT_ENABLED: subentry.data.get(CONF_MQTT_ENABLED, True),
                },
            ),
            description_placeholders={"name": subentry.title},
        )
