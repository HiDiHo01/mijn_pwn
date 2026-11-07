# coordinator.py
import asyncio
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
import logging
from typing import Callable

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.event import async_track_point_in_utc_time
from homeassistant.helpers.issue_registry import (
    IssueSeverity,
    async_create_issue,
    async_delete_issue,
)
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import (
    PWNAPI,
    PWNAPIAuthenticationError,
    PWNAPICommunicationError,
    PWNAPIError,
    PWNAPIServerError,
    PWNReadingRequestClosedError,
)
from .const import DATA_READING_REQUESTS, DATA_USER, DOMAIN

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


def _normalize_fetch_result(key: str, result: object) -> object:
    """Return one endpoint result without masking authentication or cancellation."""
    if isinstance(result, (ConfigEntryAuthFailed, PWNAPIAuthenticationError)):
        raise ConfigEntryAuthFailed("PWN authentication expired") from None
    if isinstance(result, asyncio.CancelledError):
        raise result
    if isinstance(result, Exception):
        _LOGGER.warning("Failed to fetch %s", key)
        return None
    return result


async def _fetch_contact_preferences(
    api: PWNAPI, user_data: object
) -> dict[str, object] | None:
    """Fetch contact preferences when an account relation number is present."""
    if not isinstance(user_data, dict):
        return None
    customer_id = user_data.get("relationNumber")
    if not isinstance(customer_id, str):
        return None
    try:
        return await api.get_contact_preferences(customer_id)
    except ConfigEntryAuthFailed, PWNAPIAuthenticationError:
        raise
    except Exception:
        _LOGGER.warning("Failed to fetch contact preferences")
        return None


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
        self._unloading = False
        self.draft_readings: dict[str, PWNReadingDraft] = {}
        self._closed_reading_requests: dict[str, str] = {}
        self._prepared_submissions: dict[str, PWNPreparedSubmission] = {}
        self._confirmation_cancels: dict[str, Callable[[], None]] = {}
        self.confirmed_readings: dict[str, dict[str, PWNConfirmedMeterReading]] = {}
        self.uncertain_submissions: dict[str, dict[str, datetime]] = {}
        self._confirmed_store = Store[dict[str, object]](
            hass,
            1,
            f"{config_entry.entry_id}_mijn_pwn_confirmed_readings",
            private=True,
        )
        self._draft_store = Store[dict[str, object]](
            hass,
            1,
            f"{config_entry.entry_id}_mijn_pwn_meter_drafts",
            private=True,
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

    def _draft_storage_payload(self) -> dict[str, object]:
        """Serialize draft values and metadata independently of entity availability."""
        return {
            "drafts": [
                {
                    "meter_number": meter_number,
                    "value": draft.value,
                    "entered_at": draft.entered_at.isoformat(),
                    "reading_number": draft.reading_number,
                    "reading_date": (
                        draft.reading_date.isoformat()
                        if draft.reading_date is not None
                        else None
                    ),
                }
                for meter_number, draft in self.draft_readings.items()
            ],
            "closed_requests": [
                {"meter_number": meter_number, "reading_number": reading_number}
                for meter_number, reading_number in self._closed_reading_requests.items()
            ],
        }

    @staticmethod
    def _valid_stored_draft_identity(item: dict[str, object]) -> bool:
        """Check persisted meter identifiers and numeric reading limits."""
        meter_number = item.get("meter_number")
        reading_number = item.get("reading_number")
        value = item.get("value")
        return (
            isinstance(meter_number, str)
            and bool(meter_number.strip())
            and isinstance(reading_number, str)
            and len(reading_number) == 11
            and reading_number.isascii()
            and reading_number.isdecimal()
            and type(value) is int
            and 0 <= value <= 999999999
        )

    @staticmethod
    def _parse_stored_entry_time(value: object) -> datetime | None:
        """Only restore a valid, timezone-aware draft entry timestamp."""
        if not isinstance(value, str):
            return None
        try:
            entered_at = datetime.fromisoformat(value)
        except ValueError:
            return None
        if entered_at.tzinfo is None or entered_at.utcoffset() is None:
            return None
        return entered_at

    @staticmethod
    def _parse_stored_reading_date(value: object) -> date | None:
        """Parse a stored observation date, returning None for invalid values."""
        if not isinstance(value, str):
            return None
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None

    @classmethod
    def _parse_stored_draft(cls, item: object) -> tuple[str, PWNReadingDraft] | None:
        """Restore one valid draft without allowing malformed metadata through."""
        if not isinstance(item, dict) or not cls._valid_stored_draft_identity(item):
            return None

        entered_at = cls._parse_stored_entry_time(item.get("entered_at"))
        if entered_at is None:
            return None

        date_value = item.get("reading_date")
        reading_date = cls._parse_stored_reading_date(date_value)
        if date_value is not None and reading_date is None:
            return None

        meter_number = item["meter_number"]
        return meter_number, PWNReadingDraft(
            value=item["value"],
            entered_at=entered_at,
            reading_number=item["reading_number"],
            reading_date=reading_date,
        )

    @staticmethod
    def _parse_stored_closed_request(item: object) -> tuple[str, str] | None:
        """Restore only valid, per-meter rejected request identifiers."""
        if not isinstance(item, dict):
            return None
        meter_number = item.get("meter_number")
        reading_number = item.get("reading_number")
        if (
            not isinstance(meter_number, str)
            or not meter_number.strip()
            or not isinstance(reading_number, str)
            or len(reading_number) != 11
            or not reading_number.isascii()
            or not reading_number.isdecimal()
        ):
            return None
        return meter_number, reading_number

    async def async_load_drafts(self) -> None:
        """Restore local drafts and rejected request IDs independently."""
        saved = await self._draft_store.async_load()
        if not isinstance(saved, dict):
            return

        removed_confirmed = False
        drafts = saved.get("drafts")
        if isinstance(drafts, list):
            restored: dict[str, PWNReadingDraft] = {}
            for item in drafts:
                parsed = self._parse_stored_draft(item)
                if parsed is not None:
                    meter_number, draft = parsed
                    restored[meter_number] = draft
            self.draft_readings = {
                meter_number: draft
                for meter_number, draft in restored.items()
                if not self.was_reading_submitted(meter_number, draft.reading_number)
            }
            removed_confirmed = len(self.draft_readings) != len(restored)

        closed_requests = saved.get("closed_requests")
        if isinstance(closed_requests, list):
            restored_closed: dict[str, str] = {}
            for item in closed_requests:
                parsed = self._parse_stored_closed_request(item)
                if parsed is not None:
                    meter_number, reading_number = parsed
                    restored_closed[meter_number] = reading_number
            self._closed_reading_requests = restored_closed

        if removed_confirmed:
            # Confirmations were restored first; persist the cleaned draft store
            # before registering entities or accepting any new meter input.
            await self.async_save_drafts()

    def schedule_draft_save(self) -> None:
        """Schedule durable draft metadata storage from synchronous callbacks."""
        self._draft_store.async_delay_save(self._draft_storage_payload, 1)

    async def async_save_drafts(self) -> None:
        """Persist the current drafts immediately after explicit user changes."""
        await self._draft_store.async_save(self._draft_storage_payload())

    def is_reading_request_closed(self, meter_number: str, reading_number: str) -> bool:
        """Return whether PWN rejected this exact meter and request as closed."""
        return self._closed_reading_requests.get(meter_number) == reading_number

    async def async_record_closed_reading_request(
        self, meter_number: str, reading_number: str
    ) -> None:
        """Persist a sap-01 rejection and immediately disable the affected meter."""
        if (
            self._parse_stored_closed_request(
                {"meter_number": meter_number, "reading_number": reading_number}
            )
            is None
        ):
            raise ValueError("Invalid rejected PWN meter-reading identifier")
        self._closed_reading_requests[meter_number] = reading_number
        self.invalidate_prepared_submission(meter_number)
        self.async_update_listeners()
        await self.async_save_drafts()

    def was_reading_submitted(self, meter_number: str, reading_number: str) -> bool:
        """Return whether this exact request was previously accepted by PWN."""
        return reading_number in self.confirmed_readings.get(meter_number, {})

    def is_submission_uncertain(self, meter_number: str, reading_number: str) -> bool:
        """Block a final POST while the outcome of this exact request is unknown."""
        return reading_number in self.uncertain_submissions.get(meter_number, {})

    def _uncertain_issue_id(self, meter_number: str, reading_number: str) -> str:
        """Generate an opaque repair identifier without exposing account details."""
        identity = f"{self.config_entry.entry_id}:{meter_number}:{reading_number}"
        digest = sha256(identity.encode()).hexdigest()[:20]
        return f"uncertain_meter_submission_{digest}"

    def _create_uncertain_repair(self, meter_number: str, reading_number: str) -> None:
        """Show a persistent-in-practice repair on each startup until resolved."""
        async_create_issue(
            self.hass,
            DOMAIN,
            self._uncertain_issue_id(meter_number, reading_number),
            config_entry_id=self.config_entry.entry_id,
            is_fixable=False,
            severity=IssueSeverity.ERROR,
            translation_key="uncertain_meter_submission",
        )

    def _delete_uncertain_repair(self, meter_number: str, reading_number: str) -> None:
        """Remove the repair only after a durable resolution."""
        async_delete_issue(
            self.hass, DOMAIN, self._uncertain_issue_id(meter_number, reading_number)
        )

    async def _async_mark_submission_uncertain(
        self, meter_number: str, reading_number: str
    ) -> None:
        """Commit a write-ahead marker before the final remote POST starts."""
        requests = self.uncertain_submissions.setdefault(meter_number, {})
        requests[reading_number] = datetime.now(timezone.utc)
        try:
            await self._confirmed_store.async_save(self._confirmed_storage_payload())
        except BaseException:
            requests.pop(reading_number, None)
            if not requests:
                self.uncertain_submissions.pop(meter_number, None)
            raise
        self._create_uncertain_repair(meter_number, reading_number)
        self.async_update_listeners()

    async def async_release_uncertain_submission(
        self, meter_number: str, reading_number: str
    ) -> None:
        """Manually release a request only after PWN confirms it was NOT processed."""
        async with self._submission_lock:
            if self._unloading:
                raise ValueError("PWN integration is unloading; release cancelled")
            if not self.is_submission_uncertain(meter_number, reading_number):
                raise ValueError("No unresolved meter-reading submission was found")
            requests = self.uncertain_submissions[meter_number]
            started_at = requests.pop(reading_number)
            if not requests:
                self.uncertain_submissions.pop(meter_number)
            try:
                await self._confirmed_store.async_save(
                    self._confirmed_storage_payload()
                )
            except BaseException:
                self.uncertain_submissions.setdefault(meter_number, {})[
                    reading_number
                ] = started_at
                raise
            self.invalidate_prepared_submission(meter_number)
            self._delete_uncertain_repair(meter_number, reading_number)
            self.async_update_listeners()

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
            ],
            "unresolved_submissions": [
                {
                    "meter_number": meter_number,
                    "reading_number": reading_number,
                    "started_at": started_at.isoformat(),
                }
                for meter_number, requests in self.uncertain_submissions.items()
                for reading_number, started_at in requests.items()
            ],
        }

    async def async_load_confirmed_readings(self) -> None:
        """Restore per-meter acknowledgements before platforms are forwarded."""
        saved = await self._confirmed_store.async_load()
        if not isinstance(saved, dict):
            return
        records = saved.get("readings")
        if not isinstance(records, list):
            records = []

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

        unresolved = saved.get("unresolved_submissions")
        restored_uncertain: dict[str, dict[str, datetime]] = {}
        if isinstance(unresolved, list):
            for item in unresolved:
                identity = self._parse_stored_closed_request(item)
                if identity is None:
                    continue
                started_at = self._parse_stored_entry_time(item.get("started_at"))
                if started_at is None:
                    continue
                meter_number, reading_number = identity
                if not self.was_reading_submitted(meter_number, reading_number):
                    restored_uncertain.setdefault(meter_number, {})[reading_number] = (
                        started_at
                    )
        self.uncertain_submissions = restored_uncertain
        for meter_number, requests in restored_uncertain.items():
            for reading_number in requests:
                self._create_uncertain_repair(meter_number, reading_number)

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
        while self._prepared_submissions:
            meter_number = next(iter(self._prepared_submissions))
            self.invalidate_prepared_submission(meter_number)
        for cancel in self._confirmation_cancels.values():
            cancel()
        self._confirmation_cancels.clear()

    @staticmethod
    def _date_satisfies_request_bound(
        reading_date: date, value: object, *, is_start: bool
    ) -> bool:
        """Reject malformed PWN bounds and compare against local calendar dates."""
        if value is None:
            return True
        if not isinstance(value, str):
            return False
        try:
            limit = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return False
        if limit.tzinfo is None or limit.utcoffset() is None:
            return False
        limit_date = dt_util.as_local(limit).date()
        return reading_date >= limit_date if is_start else reading_date <= limit_date

    @staticmethod
    def _reading_date_in_window(
        reading_date: date | None, request: dict[str, object]
    ) -> bool:
        """Validate an observed date against PWN's start, end and today's date."""
        if type(reading_date) is not date or reading_date > dt_util.now().date():
            return False
        return all(
            PWNDataUpdateCoordinator._date_satisfies_request_bound(
                reading_date, request.get(key), is_start=is_start
            )
            for key, is_start in (
                ("readingStartDate", True),
                ("readingDate", False),
            )
        )

    @staticmethod
    def _find_open_meter_request(
        requests: object,
        meter_number: str,
        *,
        excluded_readings: dict[str, PWNConfirmedMeterReading] | None = None,
    ) -> dict[str, object] | None:
        """Find exactly one pending open request, ignoring acknowledged IDs."""
        if not isinstance(requests, list):
            return None
        matches = [
            item
            for item in requests
            if isinstance(item, dict)
            and item.get("meterNumber") == meter_number
            and item.get("openReading") is True
            and (
                excluded_readings is None
                or item.get("readingNumber") not in excluded_readings
            )
        ]
        return matches[0] if len(matches) == 1 else None

    @staticmethod
    def _valid_draft_for_request(
        draft: PWNReadingDraft, request: dict[str, object]
    ) -> bool:
        """Validate the reading and its original request identifiers."""
        reading_number = request.get("readingNumber")
        wheel_count = request.get("amountOfWheels")
        if (
            not isinstance(reading_number, str)
            or len(reading_number) != 11
            or not reading_number.isascii()
            or not reading_number.isdecimal()
            or draft.reading_number != reading_number
            or type(wheel_count) is not int
            or not 1 <= wheel_count <= 9
        ):
            return False
        return type(draft.value) is int and 0 <= draft.value < 10**wheel_count

    @staticmethod
    def _extract_relation_number(account: object) -> str | None:
        """Validate seven relation digits, with optional observed 000 prefix."""
        if not isinstance(account, dict):
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
        return identifier if len(identifier) == 7 else None

    @staticmethod
    def _valid_meter_address(address: object) -> bool:
        """Require all PWN submission address fields as bounded strings."""
        if not isinstance(address, dict):
            return False
        return all(
            isinstance(address.get(key), str)
            and len(address[key]) <= 255
            and (key == "houseNumberAddition" or bool(address[key].strip()))
            for key in ("postalCode", "street", "houseNumber", "houseNumberAddition")
        )

    def current_submission_details(
        self, meter_number: str, *, data: dict[str, object] | None = None
    ) -> PWNSubmissionDetails | None:
        """Return a validated snapshot, optionally against fresh API data."""
        current_data = self.data if data is None else data
        draft = self.draft_readings.get(meter_number)
        if draft is None or not isinstance(current_data, dict):
            return None

        request = self._find_open_meter_request(
            current_data.get(DATA_READING_REQUESTS),
            meter_number,
            excluded_readings=self.confirmed_readings.get(meter_number),
        )
        if request is None or not self._valid_draft_for_request(draft, request):
            return None
        if self.is_reading_request_closed(
            meter_number, draft.reading_number
        ) or self.was_reading_submitted(meter_number, draft.reading_number):
            return None

        identifier = self._extract_relation_number(current_data.get(DATA_USER))
        address = request.get("address")
        if identifier is None or not self._valid_meter_address(address):
            return None

        if draft.entered_at.tzinfo is None or draft.entered_at.utcoffset() is None:
            return None
        if not self._reading_date_in_window(draft.reading_date, request):
            return None
        if not isinstance(address, dict) or type(draft.reading_date) is not date:
            return None

        return PWNSubmissionDetails(
            meter_number=meter_number,
            reading_number=draft.reading_number,
            relation_number=identifier,
            value=draft.value,
            wheel_count=request["amountOfWheels"],
            reading_date=draft.reading_date,
            entered_at=draft.entered_at,
            postal_code=address["postalCode"],
            street=address["street"],
            house_number=address["houseNumber"],
            house_number_addition=address["houseNumberAddition"],
        )

    def _invalidate_changed_preparations(self, updated_data: dict[str, object]) -> None:
        """Revoke approvals immediately if fresh PWN request data has changed."""
        for meter_number, prepared in tuple(self._prepared_submissions.items()):
            current = self.current_submission_details(meter_number, data=updated_data)
            if current != prepared.details:
                self.invalidate_prepared_submission(meter_number)

    async def async_prepare_for_unload(self) -> None:
        """Block new submissions and finish active account writes before unload."""
        self._unloading = True
        async with self._submission_lock:
            self.async_clear_pending_confirmations()
            await self.async_save_drafts()

    def resume_after_failed_unload(self) -> None:
        """Re-enable submission actions if platform unload could not finish."""
        self._unloading = False

    def is_prepared_submission(self, details: PWNSubmissionDetails) -> bool:
        """Check if the exact draft still has a live, approved confirmation."""
        prepared = self._prepared_submissions.get(details.meter_number)
        return (
            prepared is not None
            and prepared.details == details
            and datetime.now(timezone.utc) < prepared.expires_at
            and self.current_submission_details(details.meter_number) == details
            and not self.is_submission_uncertain(
                details.meter_number, details.reading_number
            )
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
            if self._unloading:
                raise ValueError("PWN integration is unloading; submission cancelled")
            self.invalidate_prepared_submission(details.meter_number)
            if self.current_submission_details(details.meter_number) != details:
                raise ValueError("Meter reading changed; enter and check it again")
            if self.was_reading_submitted(details.meter_number, details.reading_number):
                raise ValueError("This meter-reading request was already submitted")
            if self.is_submission_uncertain(
                details.meter_number, details.reading_number
            ):
                raise ValueError("Submission outcome unknown; verify with PWN first")

            try:
                await self.api.validate_measurement_request(
                    details.reading_number, details.as_address()
                )
            except PWNReadingRequestClosedError:
                await self.async_record_closed_reading_request(
                    details.meter_number, details.reading_number
                )
                raise
            if self.current_submission_details(details.meter_number) != details:
                raise ValueError("Meter reading changed during request validation")

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
            if self._unloading:
                raise ValueError("PWN integration is unloading; submission cancelled")
            if not self.is_prepared_submission(details):
                raise ValueError("Confirm the unchanged reading within two minutes")
            self.invalidate_prepared_submission(details.meter_number)
            if self.was_reading_submitted(details.meter_number, details.reading_number):
                raise ValueError("This meter-reading request was already submitted")
            if self.is_submission_uncertain(
                details.meter_number, details.reading_number
            ):
                raise ValueError("Submission outcome unknown; verify with PWN first")

            # Persist before invoking the non-idempotent final endpoint.
            await self._async_mark_submission_uncertain(
                details.meter_number, details.reading_number
            )
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
            self.uncertain_submissions[details.meter_number].pop(details.reading_number)
            if not self.uncertain_submissions[details.meter_number]:
                self.uncertain_submissions.pop(details.meter_number)
            await self._confirmed_store.async_save(self._confirmed_storage_payload())
            self._delete_uncertain_repair(details.meter_number, details.reading_number)

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
            self._invalidate_changed_preparations(data)
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

        except PWNAPIAuthenticationError:
            # Never expose upstream authentication details to Home Assistant.
            raise ConfigEntryAuthFailed("PWN authentication expired") from None

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
        """Fetch independent API datasets while isolating transient failures."""

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

        data: dict[str, object] = {
            key: _normalize_fetch_result(key, result)
            for key, result in zip(tasks, results)
        }
        if all(result is None for result in data.values()):
            raise UpdateFailed("All PWN data endpoints are unavailable")

        data["contact_preferences"] = await _fetch_contact_preferences(
            self.api, data["user_data"]
        )
        _LOGGER.debug("Received API data: %s", list(data.keys()))

        return data
