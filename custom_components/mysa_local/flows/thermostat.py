"""Thermostat discovery, pairing, takeover, and account-linking steps."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

from homeassistant.data_entry_flow import AbortFlow, FlowHandler
from homeassistant.helpers.selector import (
    BooleanSelector,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
)
from mysa_client.cloud import (
    MODEL_NAMES,
    MysaApiError,
    MysaClient,
    MysaConnectionError,
    MysaError,
)
from mysa_client.homekit import (
    AccessoryInfo,
    HomeKitAuthenticationError,
    HomeKitBusyError,
    HomeKitConnectionError,
    HomeKitDiscovery,
    HomeKitError,
    HomeKitMalformedPinError,
    HomeKitMaxPeersError,
    HomeKitMaxTriesError,
    HomeKitNotFoundError,
    HomeKitUnavailableError,
)
import voluptuous as vol

from custom_components.mysa_local.account import (
    account_client,
    async_add_to_account,
)
from custom_components.mysa_local.const import (
    LOGGER,
    WIFI_SETUP_URL,
    is_mysa_model,
)
from custom_components.mysa_local.homekit_adapter import (
    async_copy_pairing,
    async_get_controller,
    async_unpair,
)
from custom_components.mysa_local.migration import HKC_DOMAIN
from custom_components.mysa_local.pairing_store import pairing_store

from .shared import (
    CONF_ADD_TO_ACCOUNT,
    CONF_DEVICE,
    CONF_ENTRY,
    CONF_HOME,
    CONF_PAIRING_CODE,
    InsecureCodeError,
    PairingPersistenceError,
    configured_hkids,
    format_pin,
    homekit_device_candidates,
    hub_entry,
)
from .state import ThermostatFlowState


class ThermostatSteps(FlowHandler[Any, Any, Any]):
    """Pairing and account linking shared by setup, subentries, and repairs."""

    def __init__(self) -> None:
        """Give each setup, subentry, or repair flow its own thermostat state."""
        super().__init__()
        self._thermostat = ThermostatFlowState()

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
        known = self._known_hkids()
        store = pairing_store(self.hass)
        pending = await store.async_load()
        self._thermostat.pending_recoveries = {
            hkid: record for hkid, record in pending.items() if hkid not in known
        }
        if user_input is not None:
            selected = user_input.get(CONF_DEVICE)
            if selected in self._thermostat.pending_recoveries:
                record = self._thermostat.pending_recoveries[selected]
                return await self._async_resume_pending_pairing(selected, record)
        if (
            user_input is not None
            and user_input.get(CONF_DEVICE) in self._thermostat.discoveries
        ):
            discovery = self._thermostat.discoveries[user_input[CONF_DEVICE]]
            self._thermostat.hkid = discovery.id.lower()
            self._thermostat.name = discovery.name
            self._thermostat.model = discovery.model
            return await self.async_step_pair()

        self._thermostat.discoveries = {}
        async for discovery in controller.async_discover():
            if not is_mysa_model(discovery.model) or discovery.paired:
                continue
            if discovery.id.lower() in known:
                continue
            self._thermostat.discoveries[discovery.id.lower()] = discovery

        # An unpaired advertisement means the accessory was reset or had our
        # pairing removed; the saved keys can no longer be resumed.
        for hkid in (
            self._thermostat.discoveries.keys()
            & self._thermostat.pending_recoveries.keys()
        ):
            await store.async_clear(hkid)
            self._thermostat.pending_recoveries.pop(hkid)

        if not self._thermostat.discoveries and not self._thermostat.pending_recoveries:
            return self.async_show_form(
                step_id="pick",
                data_schema=vol.Schema({}),
                errors={"base": "no_devices"},
                description_placeholders={"url": WIFI_SETUP_URL},
            )
        options = [
            SelectOptionDict(
                value=hkid,
                label=(
                    f"{d.name} ({MODEL_NAMES.get(d.model, d.model)})"
                    if isinstance(d.model, str)
                    else f"{d.name} ({d.model})"
                ),
            )
            for hkid, d in self._thermostat.discoveries.items()
        ]
        options.extend(
            SelectOptionDict(value=hkid, label=self._pending_pairing_label(record))
            for hkid, record in self._thermostat.pending_recoveries.items()
        )
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

    @staticmethod
    def _pending_pairing_label(record: Mapping[str, Any]) -> str:
        """Format a pairing recovery option from potentially old stored data."""
        name = record.get("name")
        model = record.get("model")
        label = MODEL_NAMES.get(model, model) if isinstance(model, str) else "Mysa"
        return f"Resume {name if isinstance(name, str) and name else 'Mysa'} ({label})"

    def _show_pair_form(
        self,
        errors: dict[str, str] | None = None,
        error_detail: str = "",
    ) -> Any:
        model = self._thermostat.model or ""
        return self.async_show_form(
            step_id="pair",
            data_schema=vol.Schema({vol.Required(CONF_PAIRING_CODE): str}),
            errors=errors or {},
            description_placeholders={
                "name": self._thermostat.name or "Mysa",
                "model": MODEL_NAMES.get(model, model) or "Mysa",
                "url": WIFI_SETUP_URL,
                "error": error_detail,
            },
        )

    async def _async_resume_pending_pairing(
        self, hkid: str, record: Mapping[str, Any]
    ) -> Any:
        """Read a pairing saved before flow cancellation or accessory-read failure."""
        pairing_data = record.get("pairing")
        if not isinstance(pairing_data, Mapping):
            return self._show_pair_form({"base": "pairing_failed"})

        hkid = hkid.lower()
        self._thermostat.hkid = hkid
        saved_name = record.get("name")
        saved_model = record.get("model")
        self._thermostat.name = (
            saved_name if isinstance(saved_name, str) else self._thermostat.name
        )
        self._thermostat.model = (
            saved_model if isinstance(saved_model, str) else self._thermostat.model
        )
        controller = await async_get_controller(self.hass)
        device = None
        try:
            device = controller.load_pairing(dict(pairing_data))
            async with asyncio.timeout(30):
                info = await device.async_get_info()
        except HomeKitConnectionError as err:
            LOGGER.warning(
                "Couldn't resume HomeKit pairing with %s: %s",
                "[redacted]",
                type(err).__name__,
            )
            return self._show_pair_form({"base": "cannot_connect_device"}, str(err))
        except HomeKitAuthenticationError as err:
            await pairing_store(self.hass).async_clear(hkid)
            LOGGER.debug(
                "Saved HomeKit pairing is no longer accepted: %s", type(err).__name__
            )
            return self._show_pair_form({"base": "pairing_failed"}, str(err))
        except (HomeKitError, KeyError, TypeError, ValueError) as err:
            LOGGER.debug("Resuming HomeKit pairing failed: %s", type(err).__name__)
            return self._show_pair_form({"base": "pairing_failed"}, str(err))
        finally:
            if device is not None:
                try:
                    await device.async_close()
                except HomeKitError as err:
                    LOGGER.debug(
                        "Closing resumed pairing failed: %s", type(err).__name__
                    )

        await self._async_store_pairing_backup(
            hkid,
            pairing_data,
            name=info.name or self._thermostat.name,
            model=info.model or self._thermostat.model,
            migrate_from=record.get("migrate_from")
            if isinstance(record.get("migrate_from"), str)
            else None,
        )
        return await self._async_thermostat_ready(
            info, pairing_data, record.get("migrate_from")
        )

    async def _async_store_pairing_backup(
        self,
        hkid: str,
        pairing: Mapping[str, Any],
        *,
        name: str | None,
        model: str | None,
        migrate_from: str | None = None,
    ) -> None:
        """Save a newly accepted HomeKit pairing before later setup steps."""
        self._thermostat.hkid = hkid.lower()
        await asyncio.shield(
            pairing_store(self.hass).async_save(
                self._thermostat.hkid,
                pairing,
                name=name,
                model=model,
                migrate_from=migrate_from,
            )
        )

    async def _async_start_pairing(self) -> tuple[str | None, str]:
        """Open a pair-setup session; return (error key, detail)."""
        assert self._thermostat.hkid is not None
        controller = await async_get_controller(self.hass)
        try:
            discovery = await controller.async_find(self._thermostat.hkid)
            self._thermostat.finish_pairing = await discovery.async_start_pairing()
        except HomeKitBusyError:
            return "busy", ""
        except HomeKitMaxTriesError:
            return "max_tries", ""
        except HomeKitUnavailableError as err:
            raise AbortFlow("already_paired") from err
        except (HomeKitNotFoundError, HomeKitConnectionError) as err:
            # Mysa's HomeKit server sometimes restarts for a few minutes (seen
            # right after an unpair); keep the form so the user can retry.
            LOGGER.warning(
                "Couldn't start pairing with %s: %s", "[redacted]", type(err).__name__
            )
            return "cannot_connect_device", str(err)
        except (HomeKitError, IndexError) as err:
            LOGGER.debug("Starting pairing failed: %s", type(err).__name__)
            return "pairing_failed", str(err)
        return None, ""

    async def async_step_pair(self, user_input: dict[str, Any] | None = None) -> Any:
        """Enter the 8-digit HomeKit code and pair.

        Mysa's code is printed on the device, so one submit both opens the
        pair-setup session (if needed) and finishes it.
        """
        if self._thermostat.hkid is not None and (
            pending := (await pairing_store(self.hass).async_load()).get(
                self._thermostat.hkid
            )
        ):
            # A previous attempt completed HomeKit pairing but did not finish
            # reading the accessory or the user cancelled the rest of setup.
            return await self._async_resume_pending_pairing(
                self._thermostat.hkid, pending
            )

        code: str | None = None
        if user_input is not None:
            try:
                code = format_pin(user_input[CONF_PAIRING_CODE])
            except HomeKitMalformedPinError:
                return self._show_pair_form({CONF_PAIRING_CODE: "invalid_code"})
            except InsecureCodeError:
                return self._show_pair_form({CONF_PAIRING_CODE: "insecure_code"})

        if self._thermostat.finish_pairing is None:
            error, detail = await self._async_start_pairing()
            if error is not None:
                return self._show_pair_form({"base": error}, detail)

        if code is None or self._thermostat.finish_pairing is None:
            return self._show_pair_form()

        self._thermostat.pairing_in_progress = True

        async def _persist_pairing(pairing_data: Mapping[str, Any]) -> None:
            assert self._thermostat.hkid is not None
            try:
                await self._async_store_pairing_backup(
                    self._thermostat.hkid,
                    pairing_data,
                    name=self._thermostat.name,
                    model=self._thermostat.model,
                )
            except Exception as err:  # Pairing already changed the accessory.
                raise PairingPersistenceError from err

        try:
            data, info = await self._thermostat.finish_pairing.async_complete(
                code, on_pairing_saved=_persist_pairing
            )
        except PairingPersistenceError:
            LOGGER.error("Couldn't persist the HomeKit pairing: %s", "[redacted]")
            self._thermostat.finish_pairing = None
            return self._show_pair_form({"base": "pairing_failed"})
        except HomeKitAuthenticationError:
            self._thermostat.finish_pairing = None
            return self._show_pair_form({CONF_PAIRING_CODE: "wrong_code"})
        except HomeKitMaxPeersError:
            self._thermostat.finish_pairing = None
            return self._show_pair_form({"base": "max_peers"})
        except HomeKitConnectionError as err:
            LOGGER.warning(
                "Pairing with %s was interrupted: %s", "[redacted]", type(err).__name__
            )
            self._thermostat.finish_pairing = None
            return self._show_pair_form({"base": "cannot_connect_device"}, str(err))
        except HomeKitError as err:
            LOGGER.debug("Pairing failed: %s", type(err).__name__)
            self._thermostat.finish_pairing = None
            return self._show_pair_form({"base": "pairing_failed"}, str(err))
        finally:
            self._thermostat.pairing_in_progress = False
        return await self._async_thermostat_ready(info, data)

    async def async_step_takeover(
        self, user_input: dict[str, Any] | None = None
    ) -> Any:
        """Pick the HomeKit Device entry to move over."""
        candidates = homekit_device_candidates(self.hass)
        if not candidates:
            return self.async_abort(reason="no_homekit_device_entries")
        if user_input is not None:
            self._thermostat.takeover_entry_id = user_input[CONF_ENTRY]
            return await self.async_step_takeover_confirm()
        if len(candidates) == 1:
            self._thermostat.takeover_entry_id = next(iter(candidates))
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
            self.hass.config_entries.async_get_entry(self._thermostat.takeover_entry_id)
            if self._thermostat.takeover_entry_id
            else None
        )
        if entry is None or entry.domain != HKC_DOMAIN:
            return self.async_abort(reason="no_homekit_device_entries")
        errors: dict[str, str] = {}
        detail = ""
        if user_input is not None:
            hkid = str(entry.data.get("AccessoryPairingID", "")).lower()
            if pending := (await pairing_store(self.hass).async_load()).get(hkid):
                self._thermostat.hkid = hkid
                return await self._async_resume_pending_pairing(hkid, pending)

            async def _save_pairing(pairing: Mapping[str, Any]) -> None:
                hkid = str(pairing["AccessoryPairingID"]).lower()
                try:
                    await self._async_store_pairing_backup(
                        hkid,
                        pairing,
                        name=entry.title,
                        model=None,
                        migrate_from=entry.entry_id,
                    )
                except Exception as err:
                    raise PairingPersistenceError from err

            try:
                pairing, info = await async_copy_pairing(
                    self.hass, entry.data, on_pairing_saved=_save_pairing
                )
            except PairingPersistenceError:
                LOGGER.error(
                    "Couldn't persist the copied HomeKit pairing: %s", "[redacted]"
                )
                errors["base"] = "takeover_failed"
            except HomeKitMaxPeersError:
                errors["base"] = "max_peers"
            except HomeKitAuthenticationError as err:
                errors["base"] = "takeover_failed"
                detail = str(err)
            except HomeKitConnectionError:
                errors["base"] = "cannot_connect_device"
            except HomeKitError as err:
                LOGGER.debug("Takeover failed: %s", type(err).__name__)
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

    # ---- linking local and cloud (setup, thermostat and repair flows) ---

    def _account_client(self) -> MysaClient | None:
        """Client for account changes; None when the cloud isn't available."""
        return account_client(hub_entry(self.hass))

    async def _async_account_done(self) -> Any:
        """Continue after the account step (added, declined or not possible)."""
        raise NotImplementedError

    async def _async_local_skipped(self) -> Any:
        """Continue when the user doesn't pair the thermostat locally."""
        raise NotImplementedError

    async def _async_homes(self, client: MysaClient) -> dict[str, str]:
        if self._thermostat.homes is None:
            self._thermostat.homes = (await client.bootstrap()).homes
        return self._thermostat.homes

    async def async_step_account(self, user_input: dict[str, Any] | None = None) -> Any:
        """Put the thermostat on the Mysa account (offered, or asked for)."""
        assert self._thermostat.account_device is not None
        device_id, model, name = self._thermostat.account_device
        client = self._account_client()
        if client is None:
            return await self._async_account_done()
        errors: dict[str, str] = {}
        detail = ""
        homes: dict[str, str] = {}
        try:
            homes = await self._async_homes(client)
        except MysaError as err:
            errors["base"] = "cannot_connect"
            detail = str(err)
        if not errors and not homes:
            errors["base"] = "no_home"
        if user_input is not None:
            if self._thermostat.account_optional and not user_input.get(
                CONF_ADD_TO_ACCOUNT, True
            ):
                return await self._async_account_done()
            home_id = user_input.get(CONF_HOME) or (
                next(iter(homes)) if len(homes) == 1 else None
            )
            if not errors and home_id is not None:
                try:
                    await async_add_to_account(
                        self.hass,
                        client,
                        device_id=device_id,
                        model=model,
                        name=name,
                        home_id=home_id,
                    )
                except MysaConnectionError as err:
                    errors["base"] = "cannot_connect"
                    detail = str(err)
                except MysaApiError as err:
                    # Possibly on someone else's account. The status Mysa
                    # returns for that is unknown; its app goes by the message.
                    refused = (
                        err.status in (401, 403) or "not allowed" in str(err).lower()
                    )
                    errors["base"] = "claim_refused" if refused else "claim_failed"
                    detail = str(err)
                except (MysaError, ValueError) as err:
                    errors["base"] = "claim_failed"
                    detail = str(err)
                else:
                    return await self._async_account_done()
        fields: dict[Any, Any] = {}
        if self._thermostat.account_optional:
            fields[vol.Required(CONF_ADD_TO_ACCOUNT, default=True)] = BooleanSelector()
        if len(homes) > 1:
            fields[vol.Required(CONF_HOME)] = SelectSelector(
                SelectSelectorConfig(
                    options=[
                        SelectOptionDict(value=k, label=v) for k, v in homes.items()
                    ]
                )
            )
        return self.async_show_form(
            step_id="account",
            data_schema=vol.Schema(fields),
            errors=errors,
            description_placeholders={"name": name, "error": detail},
        )

    async def async_step_local_offer(
        self, user_input: dict[str, Any] | None = None
    ) -> Any:
        """Offer to pair a thermostat added from the account locally too."""
        return self.async_show_menu(
            step_id="local_offer",
            menu_options=["local_find", "local_skip"],
            description_placeholders={"name": self._thermostat.target_name or "Mysa"},
        )

    async def async_step_local_find(
        self, user_input: dict[str, Any] | None = None
    ) -> Any:
        """Find the thermostat's HomeKit advertisement, then ask for its code."""
        assert self._thermostat.target_device_id is not None
        discovery = await self._async_find_unpaired(self._thermostat.target_device_id)
        if discovery is None:
            return self.async_show_menu(
                step_id="local_not_found",
                menu_options=["local_find", "local_skip"],
                description_placeholders={
                    "name": self._thermostat.target_name or "Mysa",
                    "homekit_name": f"Mysa-{self._thermostat.target_device_id[-6:]}",
                    "url": WIFI_SETUP_URL,
                },
            )
        self._thermostat.hkid = discovery.id.lower()
        self._thermostat.name = discovery.name
        self._thermostat.model = discovery.model
        self._thermostat.finish_pairing = None
        return await self.async_step_pair()

    async def async_step_local_not_found(
        self, user_input: dict[str, Any] | None = None
    ) -> Any:
        """Menu shown when the thermostat wasn't found (look again)."""
        return await self.async_step_local_find()

    async def async_step_local_skip(
        self, user_input: dict[str, Any] | None = None
    ) -> Any:
        """Don't pair locally (now)."""
        self._thermostat.target_device_id = None
        return await self._async_local_skipped()

    async def _async_find_unpaired(self, device_id: str) -> HomeKitDiscovery | None:
        """The unpaired HomeKit advertisement of a thermostat, by its Mysa ID.

        A Mysa advertises as "Mysa-" plus the last 6 hex digits of its Wi-Fi
        MAC, which is also its Mysa ID.
        """
        controller = await async_get_controller(self.hass)
        suffix = device_id[-6:].lower()
        async for discovery in controller.async_discover():
            if discovery.paired or not is_mysa_model(discovery.model):
                continue
            if discovery.name.split(".")[0].lower().endswith(suffix):
                return discovery
        return None

    async def _async_wrong_thermostat(
        self, info: AccessoryInfo, pairing: Mapping[str, Any]
    ) -> Any | None:
        """Abort (and unpair) if pairing reached a different thermostat."""
        target = self._thermostat.target_device_id
        if target is None or not info.device_id or info.device_id == target:
            return None
        LOGGER.warning(
            "Paired %s while looking for %s; removing that pairing",
            "[redacted]",
            "[redacted]",
        )
        await async_unpair(self.hass, pairing)
        if self._thermostat.hkid is not None:
            await pairing_store(self.hass).async_clear(self._thermostat.hkid)
        return self.async_abort(
            reason="wrong_thermostat",
            description_placeholders={"name": self._thermostat.target_name or target},
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
