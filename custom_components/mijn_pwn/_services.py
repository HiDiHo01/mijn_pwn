# services.py
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN


async def async_refresh_data(hass: HomeAssistant, config_entry: ConfigEntry):
    """Service to manually refresh data."""
    coordinator = hass.data[DOMAIN][config_entry.entry_id]
    await coordinator.async_request_refresh()
