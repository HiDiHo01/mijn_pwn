# coordinator.py
import asyncio
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import logging
from typing import Callable

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.event import async_track_point_in_utc_time
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import (
    PWNAPI,
    PWNAPIAuthenticationError,
    PWNAPICommunicationError,
    PWNAPIError,
    PWNAPIServerError,
)
from .const import DATA_READING_REQUESTS, DATA_USER

_LOGGER = logging.getLogger(__name__)

# Set the desired logging level (change this dynamically if needed)
_LOGGER.setLevel(logging.INFO)  # Change to logging.DEBUG for testing

# Default update interval
DEFAULT_UPDATE_INTERVAL = timedelta(hours=12)

CONFIRMATION_WINDOW = timedelta(minutes=2)

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
    reading_date: date | None = None


@dataclass(frozen=True, slots=True)
class PWNSubmissionDetails:
    """Immutable snapshot of the exact draft and account data to submit."""

    meter_number: str
    reading_number: str
    relation_number: str
    value: int
    wheel_count: int
    reading_date: date
    entered_at: datetime
    postal_code: str
    street: str
    house_number: str
    house_number_addition: str

    def as_address(self) -> dict[str, object]:
        """Build only the expected address fields for the PWN API."""
        return {
            "postalCode": self.postal_code,
            "street": self.street,
            "houseNumber": self.house_number,
            "houseNumberAddition": self.house_number_addition,
        }


@dataclass(frozen=True, slots=True)
class PWNPreparedSubmission:
    """Short-lived confirmation for a validated and unchanged reading."""

    details: PWNSubmissionDetails
    expires_at: datetime


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
        self._prepared_submissions: dict[str, PWNPreparedSubmission] = {}
        self._confirmation_cancels: dict[str, Callable[[], None]] = {}
        self.confirmed_readings: dict[str, dict[str, PWNConfirmedMeterReading]] = {}
        self._confirmed_store = Store[dict[str, object]](
            hass, 1, f"{config_entry.entry_id}_mijn_pwn_confirmed_readings", private=True
        )

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

        reading = PWNConfirmedMeterReading(
            value=value,
            wheel_count=wheel_count,
            confirmed_at=datetime.now(timezone.utc),
            meter_number=meter_number,
            reading_number=reading_number,
        )
        self.confirmed_readings.setdefault(meter_number, {})[reading_number] = reading
        self.last_confirmed_meter_reading = reading
        self.async_update_listeners()

    def was_reading_submitted(self, meter_number: str, reading_number: str) -> bool:
        """Return whether this exact request was previously accepted by PWN."""
        return reading_number in self.confirmed_readings.get(meter_number, {})

    def _confirmed_storage_payload(self) -> dict[str, object]:
        """Serialize all acknowledged requests for durable local storage."""
        return {
            "readings": [
                {
                    "meter_number": reading.meter_number,
                    "reading_number": reading.reading_number,
                    "value": reading.value,
                    "wheel_count": reading.wheel_count,
                    "confirmed_at": reading.confirmed_at.isoformat(),
                }
                for requests in self.confirmed_readings.values()
                for reading in requests.values()
            ]
        }

    async def async_load_confirmed_readings(self) -> None:
        """Restore per-meter acknowledgements before platforms are forwarded."""
        saved = await self._confirmed_store.async_load()
        if not isinstance(saved, dict):
            return
        records = saved.get("readings")
        if not isinstance(records, list):
            return

        restored: dict[str, dict[str, PWNConfirmedMeterReading]] = {}
        for record in records:
            if not isinstance(record, dict):
                continue
            meter_number = record.get("meter_number")
            reading_number = record.get("reading_number")
            value = record.get("value")
            wheel_count = record.get("wheel_count")
            timestamp = record.get("confirmed_at")
            if (
                not isinstance(meter_number, str)
                or not meter_number.strip()
                or not isinstance(reading_number, str)
                or len(reading_number) != 11
                or not reading_number.isascii()
                or not reading_number.isdecimal()
                or type(value) is not int
                or type(wheel_count) is not int
                or not 1 <= wheel_count <= 9
                or not 0 <= value < 10**wheel_count
                or not isinstance(timestamp, str)
            ):
                continue
            try:
                confirmed_at = datetime.fromisoformat(timestamp)
            except ValueError:
                continue
            if confirmed_at.tzinfo is None or confirmed_at.utcoffset() is None:
                continue
            reading = PWNConfirmedMeterReading(
                value=value,
                wheel_count=wheel_count,
                confirmed_at=confirmed_at,
                meter_number=meter_number,
                reading_number=reading_number,
            )
            restored.setdefault(meter_number, {})[reading_number] = reading

        self.confirmed_readings = restored
        all_readings = [
            reading for requests in restored.values() for reading in requests.values()
        ]
        self.last_confirmed_meter_reading = (
            max(all_readings, key=lambda reading: reading.confirmed_at)
            if all_readings
            else None
        )

    def _schedule_confirmation_expiry(
        self, meter_number: str, expires_at: datetime
    ) -> None:
        """Update the confirm button when its approval window expires."""
        def _expire(_now: datetime) -> None:
            """Revoke a pending confirmation and refresh its button state."""
            self._confirmation_cancels.pop(meter_number, None)
            self.invalidate_prepared_submission(meter_number)

        self._confirmation_cancels[meter_number] = async_track_point_in_utc_time(
            self.hass, _expire, expires_at
        )

    def async_clear_pending_confirmations(self) -> None:
        """Cancel pending expiry callbacks during config-entry unload."""
        for meter_number in list(self._prepared_submissions):
            self.invalidate_prepared_submission(meter_number)
        for cancel in self._confirmation_cancels.values():
            cancel()
        self._confirmation_cancels.clear()

    @staticmethod
    def _reading_date_in_window(
        reading_date: date | None, request: dict[str, object]
    ) -> bool:
        """Validate an actual read date against PWN's requested date interval."""
        if type(reading_date) is not date or reading_date > dt_util.now().date():
            return False
        for key, is_start in (("readingStartDate", True), ("readingDate", False)):
            value = request.get(key)
            if value is None:
                continue
            if not isinstance(value, str):
                return False
            try:
                limit = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                return False
            if limit.tzinfo is None or limit.utcoffset() is None:
                return False
            limit_date = dt_util.as_local(limit).date()
            if is_start and reading_date < limit_date:
                return False
            if not is_start and reading_date > limit_date:
                return False
        return True

    def current_submission_details(
        self, meter_number: str
    ) -> PWNSubmissionDetails | None:
        """Return a complete, current reading snapshot or None if unsafe to send."""
        draft = self.draft_readings.get(meter_number)
        if draft is None or not isinstance(self.data, dict):
            return None
        requests = self.data.get(DATA_READING_REQUESTS)
        account = self.data.get(DATA_USER)
        if not isinstance(requests, list) or not isinstance(account, dict):
            return None

        matches = [
            item
            for item in requests
            if isinstance(item, dict)
            and item.get("meterNumber") == meter_number
            and item.get("openReading") is True
        ]
        if len(matches) != 1:
            return None
        request = matches[0]
        reading_number = request.get("readingNumber")
        wheels = request.get("amountOfWheels")
        if (
            not isinstance(reading_number, str)
            or len(reading_number) != 11
            or not reading_number.isascii()
            or not reading_number.isdecimal()
            or draft.reading_number != reading_number
            or type(wheels) is not int
            or not 1 <= wheels <= 9
            or type(draft.value) is not int
            or not 0 <= draft.value < 10**wheels
        ):
            return None

        identifier = account.get("relationNumber")
        if (
            not isinstance(identifier, str)
            or not identifier.isascii()
            or not identifier.isdecimal()
        ):
            return None
        if len(identifier) == 10 and identifier.startswith("000"):
            identifier = identifier[3:]
        if len(identifier) != 7:
            return None

        address = request.get("address")
        if not isinstance(address, dict):
            return None
        expected_keys = ("postalCode", "street", "houseNumber", "houseNumberAddition")
        if any(
            not isinstance(address.get(key), str)
            or len(address[key]) > 255
            or (key != "houseNumberAddition" and not address[key].strip())
            for key in expected_keys
        ):
            return None
        if draft.entered_at.tzinfo is None or draft.entered_at.utcoffset() is None:
            return None
        if not self._reading_date_in_window(draft.reading_date, request):
            return None

        return PWNSubmissionDetails(
            meter_number=meter_number,
            reading_number=reading_number,
            relation_number=identifier,
            value=draft.value,
            wheel_count=wheels,
            reading_date=draft.reading_date,
            entered_at=draft.entered_at,
            postal_code=address["postalCode"],
            street=address["street"],
            house_number=address["houseNumber"],
            house_number_addition=address["houseNumberAddition"],
        )

    def is_prepared_submission(self, details: PWNSubmissionDetails) -> bool:
        """Check if the exact draft still has a live, approved confirmation."""
        prepared = self._prepared_submissions.get(details.meter_number)
        return (
            prepared is not None
            and prepared.details == details
            and datetime.now(timezone.utc) < prepared.expires_at
            and self.current_submission_details(details.meter_number) == details
        )

    def invalidate_prepared_submission(self, meter_number: str) -> None:
        """Revoke a pending confirmation when the user changes their draft."""
        cancel = self._confirmation_cancels.pop(meter_number, None)
        if cancel is not None:
            cancel()
        if self._prepared_submissions.pop(meter_number, None) is not None:
            self.async_update_listeners()

    async def async_prepare_meter_reading(self, details: PWNSubmissionDetails) -> None:
        """Run PWN plausibility check and arm a short-lived confirmation.

        This action never calls the final meter submission endpoint.
        """
        async with self._submission_lock:
            self.invalidate_prepared_submission(details.meter_number)
            if self.current_submission_details(details.meter_number) != details:
                raise ValueError("Meter reading changed; enter and check it again")
            if self.was_reading_submitted(details.meter_number, details.reading_number):
                raise ValueError("This meter-reading request was already submitted")

            result = await self.api.validate_plausible_reading(
                details.reading_number,
                details.relation_number,
                details.value,
                details.reading_date,
                details.as_address(),
                details.wheel_count,
            )
            if result.get("isPlausible") is not True:
                raise ValueError("PWN did not approve this meter reading")
            if self.current_submission_details(details.meter_number) != details:
                raise ValueError("Meter reading changed during plausibility check")
            expires_at = datetime.now(timezone.utc) + CONFIRMATION_WINDOW
            self._prepared_submissions[details.meter_number] = PWNPreparedSubmission(
                details=details, expires_at=expires_at
            )
            self._schedule_confirmation_expiry(details.meter_number, expires_at)
            self.async_update_listeners()

    async def async_confirm_meter_reading(self, details: PWNSubmissionDetails) -> None:
        """Submit only an unchanged and explicitly confirmed prepared reading."""
        async with self._submission_lock:
            if not self.is_prepared_submission(details):
                raise ValueError("Confirm the unchanged reading within two minutes")
            self.invalidate_prepared_submission(details.meter_number)
            if self.was_reading_submitted(details.meter_number, details.reading_number):
                raise ValueError("This meter-reading request was already submitted")

            response = await self.api.submit_meter_reading(
                details.reading_number,
                details.value,
                details.reading_date,
                details.as_address(),
                details.wheel_count,
            )
            if response.get("success") is not True or response.get("errorText") != "":
                raise ValueError(
                    "PWN did not confirm the final meter reading submission"
                )
            self.record_confirmed_meter_submission(
                status_code=200,
                value=details.value,
                wheel_count=details.wheel_count,
                meter_number=details.meter_number,
                reading_number=details.reading_number,
            )
            await self._confirmed_store.async_save(self._confirmed_storage_payload())

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
