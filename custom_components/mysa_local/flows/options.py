"""Options and account management for the Mysa hub entry."""

from typing import Any

from homeassistant.config_entries import ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD, CONF_SCAN_INTERVAL
from homeassistant.data_entry_flow import section
from homeassistant.helpers.selector import (
    BooleanSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
)
from mysa_client.cloud import MODEL_NAMES, MysaError
import voluptuous as vol

from custom_components.mysa_local.account import can_import_cloud
from custom_components.mysa_local.cloud_coordinator import CloudData
from custom_components.mysa_local.const import (
    CONF_CLOUD_ENABLED,
    CONF_DEVICE_ID,
    CONF_FIRMWARE_INSTALL,
    CONF_LIVE_MODE,
    CONF_MODEL,
    CONF_MQTT_ENABLED,
    CONF_PAIRING,
    CONF_REFRESH_TOKEN,
    CONF_SAVE_PASSWORD,
    CONF_USER_ID,
    DEFAULT_LIVE_MODE,
    DEFAULT_SCAN_INTERVAL,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
)

from .account import SIGN_IN_SCHEMA, async_sign_in, sign_in_data, sign_in_error
from .shared import md


class MysaOptionsFlow(OptionsFlow):
    """Cloud on/off, polling, firmware install; the account; sign in or out."""

    def _cloud_data(self) -> CloudData | None:
        runtime = getattr(self.config_entry, "runtime_data", None)
        cloud = runtime.cloud if runtime else None
        return cloud.data if cloud is not None else None

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose what to change."""
        signed_in = bool(self.config_entry.data.get(CONF_REFRESH_TOKEN))
        menu = ["settings"]
        if signed_in and self._cloud_data() is not None:
            menu.append("account")
        menu.append("sign_out" if signed_in else "sign_in")
        if not signed_in:
            account = "Not signed in to Mysa; everything runs locally."
        else:
            email = self.config_entry.data.get(CONF_EMAIL)
            account = (
                f"Signed in to Mysa as **{md(email)}**."
                if email
                else ("Signed in to Mysa.")
            )
            if not self.config_entry.options.get(CONF_CLOUD_ENABLED, True):
                account += " The Mysa cloud is turned off."
            if CONF_PASSWORD in self.config_entry.data:
                account += (
                    "\n\n☑ **Password saved.** When the Mysa sign-in expires (it "
                    "seems to last about a month), Mysa Local signs in again by "
                    "itself."
                )
            else:
                account += (
                    "\n\n☐ **Password not saved.** When the Mysa sign-in expires "
                    "(it seems to last about a month), Home Assistant asks you to "
                    "sign in again."
                )
            account += " To change this, sign out and sign in again."
        return self.async_show_menu(
            step_id="init",
            menu_options=menu,
            description_placeholders={"account": account},
        )

    async def async_step_account(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """The thermostats on the Mysa account, and which are in Home Assistant.

        View only: a thermostat is removed from the account from its own
        settings, never from this list.
        """
        data = self._cloud_data()
        if data is None:
            return self.async_abort(reason="cloud_unavailable")
        records = data.records

        in_ha = {
            sub.data.get(CONF_DEVICE_ID): sub
            for sub in self.config_entry.subentries.values()
        }
        rows = [
            "| In Home Assistant | In the Mysa app | Model | Status |",
            "|---|---|---|---|",
        ]
        not_added = 0
        for device_id, record in records.items():
            model = record.model or ""
            cloud_name = record.name or device_id
            subentry = in_ha.get(device_id)
            if subentry is None and not can_import_cloud(model):
                ha_name, where = "—", "❌ Not supported"
            elif subentry is None:
                not_added += 1
                ha_name, where = "—", "⚪ Not added"
            else:
                ha_name = subentry.title
                where = (
                    "✅ Local and cloud"
                    if subentry.data.get(CONF_PAIRING)
                    else "☁️ Cloud only"
                )
            rows.append(
                f"| {md(ha_name)} | {md(cloud_name)} "
                f"| {md(MODEL_NAMES.get(model, model) or 'Mysa')} | {where} |"
            )
        for ha_id, ha_sub in in_ha.items():
            if ha_id is not None and ha_id not in records:
                sub_model = str(ha_sub.data.get(CONF_MODEL) or "")
                rows.append(
                    f"| {md(ha_sub.title)} | — "
                    f"| {md(MODEL_NAMES.get(sub_model, sub_model) or 'Mysa')} "
                    "| ⚠️ Not on the account |"
                )
        add_hint = (
            "\n\nTo add the ones marked *Not added*, use **Add thermostat** "
            "on the integration's page."
            if not_added
            else ""
        )
        # A menu with only "Back": there's nothing to submit on this page.
        return self.async_show_menu(
            step_id="account",
            menu_options=["init"],
            description_placeholders={
                "thermostats": "\n".join(rows) if len(rows) > 2 else "No thermostats.",
                "add_hint": add_hint,
            },
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
        if self.config_entry.data.get(CONF_REFRESH_TOKEN):
            fields[vol.Required(CONF_LIVE_MODE)] = BooleanSelector()
            fields[vol.Optional(CONF_MQTT_ENABLED)] = BooleanSelector()
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
                    CONF_LIVE_MODE: options.get(CONF_LIVE_MODE, DEFAULT_LIVE_MODE),
                    CONF_MQTT_ENABLED: options.get(CONF_MQTT_ENABLED, True),
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
                data = {
                    k: v
                    for k, v in self.config_entry.data.items()
                    if k != CONF_PASSWORD
                }
                data.update(
                    sign_in_data(
                        user_input,
                        token,
                        user_id,
                        save=bool(user_input.get(CONF_SAVE_PASSWORD)),
                    )
                )
                self.hass.config_entries.async_update_entry(
                    self.config_entry, data=data
                )
                return self.async_create_entry(
                    data={**self.config_entry.options, CONF_CLOUD_ENABLED: True}
                )
        return self.async_show_form(
            step_id="sign_in",
            data_schema=self.add_suggested_values_to_schema(SIGN_IN_SCHEMA, user_input),
            errors=errors,
        )

    async def async_step_sign_out(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Forget the stored Mysa sign-in (and the password, if saved)."""
        if user_input is not None:
            data = {
                k: v
                for k, v in self.config_entry.data.items()
                if k
                not in (CONF_REFRESH_TOKEN, CONF_USER_ID, CONF_EMAIL, CONF_PASSWORD)
            }
            self.hass.config_entries.async_update_entry(self.config_entry, data=data)
            return self.async_create_entry(data=dict(self.config_entry.options))
        return self.async_show_form(step_id="sign_out", data_schema=vol.Schema({}))
