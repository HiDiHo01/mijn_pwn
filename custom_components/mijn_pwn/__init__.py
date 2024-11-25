# __init__.py
import asyncio
import logging
from typing import Any, Optional, Tuple

from aiohttp import ClientError
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import PWNAPI, PWNAuth
from .const import CONFIG_CONSTANTS, DOMAIN, PLATFORMS
from .coordinator import PWNDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


def _get_credentials(config_entry: ConfigEntry) -> Optional[Tuple[str, str, Optional[str]]]:
    """Extract username, password, and auth_token from the config entry."""
    username = config_entry.data.get(CONF_USERNAME)
    password = config_entry.data.get(CONF_PASSWORD)
    auth_token = config_entry.data.get(CONFIG_CONSTANTS.get("AUTH_TOKEN"))

    if not username or not password:
        _LOGGER.error("Username or password not provided in config entry.")
        return None

    return username, password, auth_token


async def _create_api_and_coordinator(
    hass: HomeAssistant, username: str, password: str
) -> PWNDataUpdateCoordinator:
    """Helper function to create API and Data Update Coordinator."""
    _LOGGER.debug(
        "Creating API and Data Update Coordinator for user: %s", username)
    session = async_get_clientsession(hass)
    auth = PWNAuth(username, password)
    api = PWNAPI(auth, session)
    coordinator = PWNDataUpdateCoordinator(hass, api)
    return coordinator


async def async_setup(hass: HomeAssistant, config: dict[str, Any]) -> bool:
    """Set up the PWN integration."""
    _LOGGER.info("Setting up PWN component")
    # _LOGGER.debug("PWN integration setup with configuration: %s", config.)
    return True


async def async_setup_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    """Set up PWN from a config entry."""
    _LOGGER.debug("Setting up PWN entry: %s", config_entry)
    _LOGGER.debug("Setting up PWN entry data: %s", config_entry.data)
    _LOGGER.debug("Setting up PWN entry domain: %s", config_entry.domain)
    _LOGGER.debug("Setting up PWN entry unique_id: %s", config_entry.unique_id)
    _LOGGER.debug("Setting up PWN entry options: %s", config_entry.options)

    # Check if an instance already exists
    # if DOMAIN in hass.data:
    #     _LOGGER.error("An instance of the PWN integration already exists.")
    #     return False

    # Ensure the config entry has a unique_id
    if not config_entry.unique_id:
        _LOGGER.error("ConfigEntry unique_id is not set for %s",
                      config_entry.data)
        return False
    _LOGGER.info("ConfigEntry unique_id: %s", config_entry.unique_id)

    # Store coordinator in hass.data
    hass.data.setdefault(DOMAIN, {})
    try:
        credentials = _get_credentials(config_entry)
        if not credentials:
            return False

        username, password, auth_token = credentials
        coordinator = await _create_api_and_coordinator(hass, username, password)
        hass.data[DOMAIN][config_entry.entry_id] = coordinator

        _LOGGER.debug("Calling config_entry_first_refresh")
        await coordinator.async_config_entry_first_refresh()
    except ClientError as e:
        _LOGGER.error("Network error setting up PWN entry: %s",
                      e, exc_info=True)
        raise ConfigEntryNotReady from e
    except Exception as e:
        _LOGGER.error("Unexpected error setting up PWN entry: %s",
                      e, exc_info=True)
        return False

    await hass.config_entries.async_forward_entry_setups(config_entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    """Handle removal of an entry."""
    if unloaded := await hass.config_entries.async_unload_platforms(config_entry, PLATFORMS):
        hass.data[DOMAIN].pop(config_entry.entry_id, None)
        # If no more entries exist, remove the DOMAIN data entirely
        if not hass.data[DOMAIN]:
            hass.data.pop(DOMAIN)
    return unloaded


async def async_reload_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> None:
    """Reload config entry."""
    await async_unload_entry(hass, config_entry)
    await async_setup_entry(hass, config_entry)


async def authenticate(hass: HomeAssistant, config_entry: ConfigEntry) -> Optional[str]:
    """
    Authenticate with the PWN API and return the access token.

    Args:
        hass (HomeAssistant): The Home Assistant instance.

    Returns:
        Optional[str]: The authentication token if successful, otherwise None.
    """
    # Extract username and password from the Home Assistant configuration
    # Assuming there's only one entry for simplicity
    # config_entry = hass.config_entries.async_entries(DOMAIN)[0]
    credentials = _get_credentials(config_entry)
    if not credentials:
        return None

    username, password, _ = credentials
    try:
        coordinator = await _create_api_and_coordinator(hass, username, password)
        auth = coordinator.api.auth

        success = await asyncio.wait_for(auth.login(coordinator.api.session), timeout=10)
        if success and auth.access_token:
            _LOGGER.info("Successfully authenticated and obtained token.")
            return auth.access_token
        else:
            _LOGGER.error("Failed to authenticate.")
            return None
    except asyncio.TimeoutError:
        _LOGGER.error("Authentication timed out.")
        return None
    except Exception as e:
        _LOGGER.error(
            "Unexpected error during authentication: %s", e, exc_info=True)
        return None
