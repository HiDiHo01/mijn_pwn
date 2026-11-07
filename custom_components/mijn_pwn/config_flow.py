"""Handle the config flow for PWN."""
# config_flow

import asyncio
from collections.abc import Mapping
import logging
from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import COMPONENT_TITLE, DOMAIN, NETWORK_CONFIG

_LOGGER = logging.getLogger(__name__)
AUTH_URL = f"{NETWORK_CONFIG['API_URL']}/api/auth/login"
TIMEOUT = NETWORK_CONFIG["API_TIMEOUT"]
AUTH_SERVICE_UNAVAILABLE = "Mijn PWN authentication service unavailable"


class CannotConnect(HomeAssistantError):
    """Mijn PWN authentication is temporarily unavailable."""


@callback
def configured_instances(hass):
    """Return a set of configured PWN instances."""
    return set(
        entry.data["username"] for entry in hass.config_entries.async_entries(DOMAIN)
    )


# Register the config flow handler for the domain


@config_entries.HANDLERS.register(DOMAIN)
class PWNConfigFlow(config_entries.ConfigFlow):
    """Handle a config flow for PWN."""

    VERSION = 1
    CONNECTION_CLASS = config_entries.CONN_CLASS_CLOUD_POLL

    async def async_step_user(self, user_input=None):
        """Handle the initial step."""
        errors = {}

        # Check if an entry for this domain already exists
        # if self._async_current_entries():
        #     return self.async_abort(reason="single_instance_allowed")

        data_schema = vol.Schema(
            {
                vol.Required("username"): str,
                vol.Required("password"): str,
            }
        )

        if user_input is not None:
            username = user_input.get("username")
            # if username in configured_instances(self.hass):
            #     errors["base"] = "already_configured"
            # return self.async_show_form(
            #     step_id="user",
            #     data_schema=data_schema,
            #     errors=errors
            # )
            # Set the unique_id to the username or another unique identifier
            await self.async_set_unique_id(username)
            self._abort_if_unique_id_configured()

            try:
                if await self._validate_user_input(user_input):
                    return self.async_create_entry(
                        title=COMPONENT_TITLE, data=user_input
                    )
            except CannotConnect:
                errors["base"] = "cannot_connect"
            else:
                errors["base"] = "invalid_auth"

        return self.async_show_form(
            step_id="user", data_schema=data_schema, errors=errors
        )

    async def async_step_reauth(
        self, _entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Start password renewal for the entry selected by Home Assistant."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, str] | None = None
    ) -> ConfigFlowResult:
        """Validate a replacement password without changing account identity."""
        entry = self._get_reauth_entry()
        username = entry.data.get(CONF_USERNAME)
        if (
            not isinstance(username, str)
            or not username.strip()
            or entry.unique_id != username
        ):
            return self.async_abort(reason="reauth_account_mismatch")

        errors: dict[str, str] = {}
        data_schema = vol.Schema({vol.Required(CONF_PASSWORD): str})
        if user_input is not None:
            password = user_input.get(CONF_PASSWORD)
            if isinstance(password, str) and password:
                credentials = {CONF_USERNAME: username, CONF_PASSWORD: password}
                try:
                    if await self._validate_user_input(credentials):
                        return self.async_update_reload_and_abort(
                            entry, data_updates={CONF_PASSWORD: password}
                        )
                except CannotConnect:
                    errors["base"] = "cannot_connect"
                else:
                    errors["base"] = "invalid_auth"
            else:
                errors["base"] = "invalid_auth"

        return self.async_show_form(
            step_id="reauth_confirm", data_schema=data_schema, errors=errors
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, str] | None = None
    ) -> ConfigFlowResult:
        """Update only the selected entry, preserving its account identity."""
        errors: dict[str, str] = {}
        data_schema = vol.Schema(
            {
                vol.Required(CONF_USERNAME): str,
                vol.Required(CONF_PASSWORD): str,
            }
        )

        if user_input is not None:
            username = user_input.get(CONF_USERNAME)
            password = user_input.get(CONF_PASSWORD)

            if (
                isinstance(username, str)
                and username.strip()
                and isinstance(password, str)
                and password
            ):
                # Reject account swaps before authenticating or changing entries.
                await self.async_set_unique_id(username)
                self._abort_if_unique_id_mismatch()

                try:
                    if await self._validate_user_input(user_input):
                        return self.async_update_reload_and_abort(
                            self._get_reconfigure_entry(),
                            data_updates={
                                CONF_USERNAME: username,
                                CONF_PASSWORD: password,
                            },
                        )
                except CannotConnect:
                    errors["base"] = "cannot_connect"
                else:
                    errors["base"] = "invalid_auth"
            else:
                errors["base"] = "invalid_auth"

        return self.async_show_form(
            step_id="reconfigure", data_schema=data_schema, errors=errors
        )

    async def _validate_user_input(self, user_input: dict[str, str]) -> bool:
        """Reject invalid credentials; raise CannotConnect for temporary failures."""
        username = user_input.get(CONF_USERNAME)
        password = user_input.get(CONF_PASSWORD)

        if not username or not password:
            return False

        try:
            session = async_get_clientsession(self.hass)
            async with session.post(
                AUTH_URL,
                json={"user": username, "password": password},
                timeout=TIMEOUT,
            ) as response:
                if response.status in (401, 403):
                    return False
                if response.status != 200:
                    raise CannotConnect(AUTH_SERVICE_UNAVAILABLE)

                data = await response.json()
                if not isinstance(data, dict):
                    raise CannotConnect("Invalid authentication response")
                return isinstance(data.get("accessToken"), str) and bool(
                    data["accessToken"]
                )
        except aiohttp.ClientResponseError as error:
            if error.status in (401, 403):
                return False
            raise CannotConnect(AUTH_SERVICE_UNAVAILABLE) from None
        except aiohttp.ClientError, asyncio.TimeoutError, ValueError, TypeError:
            raise CannotConnect(AUTH_SERVICE_UNAVAILABLE) from None
