"""Mysa sign-in helpers and reauthentication flow."""

from collections.abc import Mapping
from typing import Any

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    BooleanSelector,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
import voluptuous as vol

from custom_components.mysa_local.const import (
    CONF_REFRESH_TOKEN,
    CONF_SAVE_PASSWORD,
    CONF_USER_ID,
    LOGGER,
    USER_AGENT,
)
from custom_components.mysa_local.protocols.cloud import (
    Bootstrap,
    MysaAuth,
    MysaAuthError,
    MysaClient,
    MysaConnectionError,
    MysaError,
    MysaInvalidCredentialsError,
    MysaUnsupportedChallengeError,
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

# A first sign-in also asks whether to save the password. The choice holds
# until the user signs out; re-authentication keeps it.
SIGN_IN_SCHEMA = CREDENTIALS_SCHEMA.extend(
    {vol.Optional(CONF_SAVE_PASSWORD, default=False): BooleanSelector()}
)


def sign_in_data(
    user_input: Mapping[str, Any], token: str, user_id: str | None, *, save: bool
) -> dict[str, Any]:
    """Entry data for a successful sign-in, with the password only if saved."""
    data: dict[str, Any] = {
        CONF_EMAIL: user_input[CONF_EMAIL],
        CONF_REFRESH_TOKEN: token,
        CONF_USER_ID: user_id,
    }
    if save:
        data[CONF_PASSWORD] = user_input[CONF_PASSWORD]
    return data


class MysaReauthFlow(ConfigFlow):
    """Reauthenticate a Mysa account without registering another flow domain."""

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
                # Keep the sign-in's save-password choice; a saved password is
                # replaced by the one just entered.
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates=sign_in_data(
                        user_input,
                        token,
                        user_id,
                        save=CONF_PASSWORD in entry.data,
                    ),
                )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=self.add_suggested_values_to_schema(
                CREDENTIALS_SCHEMA, {CONF_EMAIL: entry.data.get(CONF_EMAIL, "")}
            ),
            errors=errors,
        )
