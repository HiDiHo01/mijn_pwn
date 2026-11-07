# coordinator.py
import asyncio
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    PWNAPI,
    PWNAPIAuthenticationError,
    PWNAPICommunicationError,
    PWNAPIError,
    PWNAPIServerError,
)

_LOGGER = logging.getLogger(__name__)

# Set the desired logging level (change this dynamically if needed)
_LOGGER.setLevel(logging.INFO)  # Change to logging.DEBUG for testing

# Default update interval
DEFAULT_UPDATE_INTERVAL = timedelta(hours=12)

# Adjust the interval if logging level is set to DEBUG
if _LOGGER.getEffectiveLevel() == logging.DEBUG:
    DEFAULT_UPDATE_INTERVAL = timedelta(minutes=5)

_LOGGER.info("Effective logging level: %s", _LOGGER.getEffectiveLevel())
_LOGGER.debug("Update interval set to %s", DEFAULT_UPDATE_INTERVAL)


@dataclass(frozen=True, slots=True)
class PWNConfirmedMeterReading:
    """A reading acknowledged by the final PWN submission endpoint."""

    value: int
    wheel_count: int
    confirmed_at: datetime
    meter_number: str
    reading_number: str


@dataclass(frozen=True, slots=True)
class PWNReadingDraft:
    """A locally entered draft that must not be treated as submitted."""

    value: int
    entered_at: datetime
    reading_number: str


class PWNDataUpdateCoordinator(DataUpdateCoordinator):
    """Class to manage fetching data from the PWN API."""

    def __init__(self, hass: HomeAssistant, api: PWNAPI, config_entry: ConfigEntry):
        """Initialize the coordinator."""

        super().__init__(
            hass=hass,
            logger=_LOGGER,
            name="PWN Data Update Coordinator",
            update_interval=DEFAULT_UPDATE_INTERVAL,
            config_entry=config_entry,
        )
        self.api = api
        self.last_confirmed_meter_reading: PWNConfirmedMeterReading | None = None
        self._submission_lock = asyncio.Lock()
        self.draft_readings: dict[str, PWNReadingDraft] = {}

    def record_confirmed_meter_submission(
        self,
        *,
        status_code: int,
        value: int,
        wheel_count: int,
        meter_number: str,
        reading_number: str,
    ) -> None:
        """Record only a successful response from the final submission API.

        Only invoke after the final WaterUsage POST returns HTTP 200 with
        success=true and an empty errorText. Plausibility and earlier API
        responses must never count as submission acknowledgements.
        """
        if type(status_code) is not int or status_code not in (200, 204):
            raise ValueError(
                "Meter reading is not confirmed: expected final submission status "
                "200 or 204"
            )
        if (
            type(value) is not int
            or type(wheel_count) is not int
            or not 1 <= wheel_count <= 9
            or not 0 <= value < 10**wheel_count
        ):
            raise ValueError("Confirmed meter reading is outside the meter range")
        if not isinstance(meter_number, str) or not meter_number.strip():
            raise ValueError("Confirmed meter number must be a non-empty string")
        if (
            not isinstance(reading_number, str)
            or len(reading_number) != 11
            or not reading_number.isascii()
            or not reading_number.isdecimal()
        ):
            raise ValueError("Confirmed opname number must contain 11 ASCII digits")

        self.last_confirmed_meter_reading = PWNConfirmedMeterReading(
            value=value,
            wheel_count=wheel_count,
            confirmed_at=datetime.now(timezone.utc),
            meter_number=meter_number,
            reading_number=reading_number,
        )
        self.async_update_listeners()

    async def async_submit_meter_reading(
        self,
        *,
        reading_number: str,
        relation_number: str,
        meter_number: str,
        meter_reading: int,
        wheel_count: int,
        reading_date: date,
        address: dict[str, object],
    ) -> None:
        """Check plausibility, submit once, then update confirmed sensors.

        This action must be explicitly triggered by the user. On errors or
        unknown results, the last confirmed reading remains unchanged.
        """
        async with self._submission_lock:
            previous = self.last_confirmed_meter_reading
            if previous is not None and previous.reading_number == reading_number:
                raise ValueError("This meter-reading request was already submitted")

            await self.api.validate_plausible_reading(
                reading_number,
                relation_number,
                meter_reading,
                reading_date,
                address,
                wheel_count,
            )
            await self.api.submit_meter_reading(
                reading_number,
                meter_reading,
                reading_date,
                address,
                wheel_count,
            )
            self.record_confirmed_meter_submission(
                status_code=200,
                value=meter_reading,
                wheel_count=wheel_count,
                meter_number=meter_number,
                reading_number=reading_number,
            )

    async def _async_update_data(self) -> dict[str, object]:
        """Fetch fresh data from Mijn PWN.

        Raises
        ------
        ConfigEntryAuthFailed
            When credentials or refresh token are invalid.
        UpdateFailed
            For transient network, server, or unexpected errors.
        """
        try:
            _LOGGER.debug("Fetching PWN data")

            data = await self._fetch_all_data()
            data["last_update"] = datetime.now(timezone.utc).isoformat()
            return data

        except ConfigEntryAuthFailed:
            # Triggers Home Assistant re-auth flow
            raise

        except (
            PWNAPIServerError,
            PWNAPICommunicationError,
        ) as err:
            # Transient failures → mark entities unavailable
            raise UpdateFailed(err) from err

        except PWNAPIAuthenticationError as err:
            # Explicit auth failure during runtime
            raise ConfigEntryAuthFailed(err) from err

        except UpdateFailed:
            # Already correct; do not rewrap
            raise

        except Exception as err:  # pylint: disable=broad-except
            _LOGGER.exception("Unexpected error during PWN data update")
            raise UpdateFailed(err) from err

    async def old_async_update_data(self):
        """Fetch data from the API."""
        try:
            _LOGGER.debug("Fetching PWN data")
            data = await self._fetch_all_data()

            if data:
                data["last_update"] = datetime.now(timezone.utc).isoformat()
                self.async_set_updated_data(data)
            return self.data

        except ConfigEntryAuthFailed:
            # Bubble up so HA starts the re‑auth flow
            raise
        except UpdateFailed as e:
            _LOGGER.error("Failed to update data: %s", e)
            raise
        except PWNAPIServerError as err:
            # 5xx – transient
            raise UpdateFailed(err) from err
        except PWNAPICommunicationError as err:
            # Network / timeout – transient
            raise UpdateFailed(err) from err
        except PWNAPIAuthenticationError as exception:
            _LOGGER.error("Authentication failed: %s", exception)
            raise ConfigEntryAuthFailed(exception) from exception
        except PWNAPIError as err:
            _LOGGER.warning("API error: %s", err)
            raise UpdateFailed(err) from err
        except Exception as e:
            _LOGGER.error("Failed to update data: %s", e)
            raise UpdateFailed(e) from e

    async def _fetch_all_data(self) -> dict[str, object]:
        """Fetch all API data required by the coordinator."""

        tasks = {
            "invoices": self.api.get_invoices(),
            "user_data": self.api.get_relation_data(),
            "consumption_history": self.api.get_consumption_history(),
            "reading_requests": self.api.get_reading_request(),
            "payment_info": self.api.get_payment_info(),
            "advance": self.api.get_advance(),
            "notifications": self.api.get_notifications(),
        }

        results = await asyncio.gather(*tasks.values(), return_exceptions=True)

        data: dict[str, object] = {}
        for key, result in zip(tasks.keys(), results):
            if isinstance(result, Exception):
                _LOGGER.warning("Failed to fetch %s: %s", key, result)
                data[key] = None
            else:
                data[key] = result

        invoices = data.get("invoices")
        user_data = data.get("user_data")
        consumption_history = data.get("consumption_history")

        # Validate required data
        if not invoices:
            _LOGGER.warning("Incomplete invoices data received from API")
            return {}

        if not user_data:
            _LOGGER.warning("Incomplete user data received from API")
            return {}

        if not consumption_history:
            _LOGGER.warning("Incomplete consumption history received from API")
            return {}

        # Contact preferences depend on customer_id
        contact_preferences: dict[str, object] | None = None

        if isinstance(user_data, dict):
            customer_id = user_data.get("relationNumber")

            if isinstance(customer_id, str):
                try:
                    contact_preferences = await self.api.get_contact_preferences(
                        customer_id
                    )
                except Exception as e:
                    _LOGGER.warning("Failed to fetch contact preferences: %s", e)

        data["contact_preferences"] = contact_preferences
        _LOGGER.debug("Received API data: %s", list(data.keys()))

        return data
