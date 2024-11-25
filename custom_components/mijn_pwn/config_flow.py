"""Handle the config flow for PWN."""
# config_flow

import logging

import aiohttp
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import COMPONENT_TITLE, DOMAIN, NETWORK_CONFIG

_LOGGER = logging.getLogger(__name__)
AUTH_URL = f"{NETWORK_CONFIG['API_URL']}/api/auth/login"
TIMEOUT = NETWORK_CONFIG['API_TIMEOUT']


@callback
def configured_instances(hass):
    """Return a set of configured PWN instances."""
    return set(entry.data['username']
               for entry in hass.config_entries.async_entries(DOMAIN))

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

        data_schema = vol.Schema({
            vol.Required("username"): str,
            vol.Required("password"): str,
        })

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

            if await self._validate_user_input(user_input):
                return self.async_create_entry(
                    title=COMPONENT_TITLE,
                    data=user_input
                )
            errors["base"] = "invalid_auth"

        return self.async_show_form(
            step_id="user",
            data_schema=data_schema,
            errors=errors
        )

    async def async_step_reconfigure(self, user_input=None):
        """Handle the reconfiguration step."""
        errors = {}

        data_schema = vol.Schema({
            vol.Required("username"): str,
            vol.Required("password"): str,
        })

        if user_input:
            username = user_input.get("username")

            # Validate the provided credentials
            if await self._validate_user_input(user_input):
                # Update the existing entry with new credentials
                entry = self._async_current_entries()[0]
                self.hass.config_entries.async_update_entry(
                    entry, data=user_input
                )
                _LOGGER.info(
                    "Reconfiguration successful for user: %s", username)
                await self.async_set_unique_id(username)
                return self.async_create_entry(
                    title=COMPONENT_TITLE,
                    data=user_input
                )
            errors["base"] = "invalid_auth"

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=data_schema,
            errors=errors
        )

    async def _validate_user_input(self, user_input):
        """Validate user input by performing authentication."""
        username = user_input.get("username")
        password = user_input.get("password")

        if not username or not password:
            _LOGGER.error("Username or password not provided")
            return False

        try:
            session = async_get_clientsession(self.hass)
            payload = {
                "user": username,
                "password": password
            }
            async with session.post(AUTH_URL,
                                    json=payload,
                                    timeout=TIMEOUT
                                    ) as response:
                if response.status == 200:
                    data = await response.json()
                    if data.get("accessToken") is not None:
                        _LOGGER.info(
                            "Authentication successful for user: %s", username)
                        return True
                    _LOGGER.error(
                        "No access token received for user: %s", username)
                elif response.status == 401:
                    _LOGGER.error(
                        "Authentication failed for user: %s, invalid credentials", username)
                else:
                    _LOGGER.error(
                        "Authentication failed for user: %s, status code: %s",
                        username,
                        response.status
                    )
                return False
        except aiohttp.ClientResponseError as error:
            _LOGGER.error(
                "HTTP error occurred during authentication for user: %s, error: %s", username, error)
            return False
        except aiohttp.ClientError as error:
            _LOGGER.error(
                "Network error occurred during authentication for user: %s, error: %s", username, error)
            return False
        except Exception as error:
            _LOGGER.error(
                "Unexpected error occurred during authentication for user: %s, error: %s", username, error)
            return False
