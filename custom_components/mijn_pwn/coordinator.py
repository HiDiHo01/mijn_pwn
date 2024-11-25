# coordinator.py
import logging
from datetime import datetime, timedelta, timezone

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import PWNAPI, PWNAPIAuthenticationError, PWNAPIError
from .const import API_ENDPOINTS

_LOGGER = logging.getLogger(__name__)

# Set the desired logging level (change this dynamically if needed)
_LOGGER.setLevel(logging.INFO)  # Change to logging.DEBUG for testing

# Default update interval
DEFAULT_UPDATE_INTERVAL = timedelta(days=1)

# Adjust the interval if logging level is set to DEBUG
if _LOGGER.getEffectiveLevel() == logging.DEBUG:
    DEFAULT_UPDATE_INTERVAL = timedelta(minutes=5)

_LOGGER.info("Effective logging level: %s", _LOGGER.getEffectiveLevel())
_LOGGER.debug("Update interval set to %s", DEFAULT_UPDATE_INTERVAL)


class PWNDataUpdateCoordinator(DataUpdateCoordinator):
    """Class to manage fetching data from the PWN API."""

    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, api: PWNAPI):
        """Initialize the coordinator."""
        self.api = api

        super().__init__(
            hass=hass,
            logger=_LOGGER,
            name="PWN Data Update Coordinator",
            update_interval=DEFAULT_UPDATE_INTERVAL,
        )

    async def _async_update_data(self):
        """Fetch data from the API."""
        try:
            _LOGGER.debug("Fetching PWN data")
            data = await self._fetch_all_data()

            if data:
                data["last_update"] = datetime.now(timezone.utc).isoformat()
                self.async_set_updated_data(data)
            return self.data

        except UpdateFailed as e:
            _LOGGER.error("Failed to update data: %s", e)
            raise
        except PWNAPIAuthenticationError as exception:
            _LOGGER.error("Authentication failed: %s", exception)
            raise ConfigEntryAuthFailed(exception) from exception
        except PWNAPIError as exception:
            _LOGGER.error("API error: %s", exception)
            raise UpdateFailed(exception) from exception
        except Exception as e:
            _LOGGER.error("Failed to update data: %s", e)
            raise UpdateFailed(e) from e

    async def _fetch_all_data(self):
        invoices = await self.api.fetch_data_from_api(
            API_ENDPOINTS['invoices_summary']
        )
        user_data = await self.api.fetch_data_from_api(
            API_ENDPOINTS['relation_data']
        )
        consumption_history = await self.api.fetch_data_from_api(
            API_ENDPOINTS['consumption_history']
        )
        payment_info = await self.api.fetch_data_from_api(
            API_ENDPOINTS['payment_info']
        )
        advance = await self.api.fetch_data_from_api(
            API_ENDPOINTS['advance']
        )
        notifications = await self.api.fetch_data_from_api(
            API_ENDPOINTS['notifications']
        )

        if invoices is None or not invoices:
            _LOGGER.warning(
                "No or incomplete invoices data received from API")
        if user_data is None or not user_data:
            _LOGGER.warning("No or incomplete user data received from API")
        if consumption_history is None or not consumption_history:
            _LOGGER.warning(
                "No or incomplete consumption history data received from API")

        if not invoices:
            _LOGGER.warning("Incomplete invoices data received from API")
            return {}

        if not user_data:
            _LOGGER.warning("Incomplete user_data data received from API")
            return {}

        if not consumption_history:
            _LOGGER.warning(
                "Incomplete consumption_history data received from API")
            return {}

        customer_id = user_data.get('relationNumber', None)

        if customer_id is not None:
            contact_preferences = await self.api.fetch_data_from_api(
                f"{API_ENDPOINTS['contact_preferences']}/{customer_id}"
            )

        # relation_data
        # invoices_summary
        # takeover??
        data = {
            "invoices": invoices,
            "user_data": user_data,
            "consumption_history": consumption_history,
            "contact_preferences": contact_preferences,
            "payment_info": payment_info,
            "advance": advance,
            "notifications": notifications
        }
        _LOGGER.debug("Received data: %s", data)
        return data
