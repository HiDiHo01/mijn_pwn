import asyncio
from datetime import date
import logging
from math import isfinite
from typing import Literal, Optional, TypedDict, overload

from aiohttp import ClientError, ClientResponse, ClientSession

from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed

from .const import API_TIMEOUT, AUTH_ENDPOINT, DATA_ENDPOINTS

_LOGGER = logging.getLogger(__name__)


# ------------------------------------------------------------------ #
# Exceptions
# ------------------------------------------------------------------ #
class PWNAPIError(Exception):
    """Base exception for PWN API errors."""


class PWNReadingSubmissionRejected(PWNAPIError):
    """PWN explicitly rejected the final water meter reading submission."""


class PWNReadingNotPlausibleError(PWNAPIError):
    """The entered reading failed PWN plausibility checks; nothing was submitted."""

    def __init__(
        self,
        min_reading: int | float | None,
        max_reading: int | float | None,
    ) -> None:
        """Preserve optional limits without exposing account data in the error."""
        self.min_reading = min_reading
        self.max_reading = max_reading
        super().__init__(
            "PWN marked the meter reading as implausible; no reading was submitted"
        )


class PWNAPIServerError(PWNAPIError):
    """Server-side API error."""


class PWNAPICommunicationError(PWNAPIError):
    """Communication failure with the API."""


class PWNAPIAuthenticationError(PWNAPIError):
    """Authentication error."""


class PWNFinalReadingRequest(TypedDict):
    """Confirmed case-sensitive JSON keys for the final WaterUsage POST."""

    MeterReading: int
    AnomalyReason: None
    ReadingDate: str
    PostalCode: str
    Street: str
    HouseNumber: str
    HouseNumberAddition: str
    WaterMeterImage: None


class PWNFinalReadingResponse(TypedDict):
    """Business-level acknowledgement returned after final meter submission."""

    success: bool
    errorText: str


class PWNReadingPlausibilityResponse(TypedDict):
    """Plausibility decision and optional numeric reading bounds."""

    isPlausible: bool
    minReading: int | float | None
    maxReading: int | float | None


class PWNReadingPlausibilityRequest(TypedDict):
    """JSON body for checking an entered PWN water meter reading."""

    meterReading: int
    readingDate: str
    postalCode: str
    houseNumber: str
    houseNumberAddition: str


class PWNMeasurementValidationRequest(TypedDict):
    """Address fields used to validate a water meter reading request."""

    houseNumber: str
    houseNumberAddition: str
    postalCode: str


class PWNMeasurementValidationResponse(TypedDict):
    """Validation response including a short-lived supplemental data token."""

    relationNumber: str
    clientType: int
    additionalDataToken: str
    readingReason: int
    periodStartDate: str | None
    periodEndDate: str | None
    houseNumberAddition: str


class PWNWaterMeterDetailsRequest(TypedDict):
    """Address fields accepted by the PWN water meter details endpoint."""

    postalCode: str
    street: str
    houseNumber: str
    houseNumberAddition: str


class PWNWaterMeterDetails(TypedDict):
    """Meter dial configuration from the Mijn PWN WaterUsage API."""

    meterType: str
    amountOfBlackWheels: int
    amountOfRedWheels: int


class PWNMoveHouseAddress(TypedDict):
    """Address fields returned by the Mijn PWN MoveHouse validation API."""

    street: str
    houseNumber: str
    houseNumberAddition: str
    postalCode: str
    city: str
    country: str


# ------------------------------------------------------------------ #
# Authentication
# ------------------------------------------------------------------ #
class PWNAuth:
    """Handles Mijn PWN authentication."""

    def __init__(self, username: str, password: str) -> None:
        self.username = username
        self.password = password
        self.access_token: Optional[str] = None
        self.refresh_token: Optional[str] = None

    async def login(self, session: ClientSession) -> bool:
        """Authenticate and retrieve access/refresh tokens."""
        payload = {"user": self.username, "password": self.password}

        try:
            # Native asyncio.timeout
            async with asyncio.timeout(API_TIMEOUT):
                async with session.post(AUTH_ENDPOINT["login"], json=payload) as resp:
                    if resp.status == 200:
                        tokens = await resp.json()
                        self.access_token = tokens.get("accessToken")
                        self.refresh_token = tokens.get("refreshToken")
                        _LOGGER.info("PWN login successful")
                        return True
                    if resp.status in (401, 403):
                        _LOGGER.error("Invalid credentials")
                    else:
                        _LOGGER.error("Login failed. Status: %s", resp.status)
                    return False

        except asyncio.TimeoutError:
            _LOGGER.error("Timeout during login")
            return False
        except ClientError as err:
            _LOGGER.error("Client error during login: %s", err)
            return False
        except Exception as err:
            _LOGGER.exception("Unexpected error during login: %s", err)
            return False

    async def refresh_tokens(self, session: ClientSession) -> None:
        """Refresh access/refresh tokens."""
        if not self.refresh_token:
            _LOGGER.error("No refresh token available. Login first.")
            return

        payload = {
            "user": self.username,
            "password": self.password,
            "refresh_token": self.refresh_token,
        }

        try:
            async with asyncio.timeout(API_TIMEOUT):
                async with session.post(AUTH_ENDPOINT["login"], json=payload) as resp:
                    if resp.status == 200:
                        tokens = await resp.json()
                        self.access_token = tokens.get("accessToken")
                        self.refresh_token = tokens.get("refreshToken")
                        _LOGGER.info("Token refresh successful")
                    elif resp.status in (401, 403):
                        _LOGGER.error("Refresh token expired or invalid")
                        raise ConfigEntryAuthFailed
                    else:
                        _LOGGER.error(
                            "Failed to refresh tokens. Status: %s", resp.status
                        )

        except asyncio.TimeoutError:
            _LOGGER.error("Timeout during token refresh")
        except ClientError as err:
            _LOGGER.error("Client error during token refresh: %s", err)
        except ConfigEntryAuthFailed:
            raise
        except Exception as err:
            _LOGGER.exception("Unexpected error during token refresh: %s", err)

    def is_authenticated(self) -> bool:
        """Return True if a valid access token is available."""
        return self.access_token is not None

    def logout(self) -> None:
        """Clear authentication tokens."""
        self.access_token = None
        self.refresh_token = None
        _LOGGER.info("Logout successful")


# ------------------------------------------------------------------ #
# API Client
# ------------------------------------------------------------------ #
class PWNAPI:
    """Interact with Mijn PWN API."""

    def __init__(
        self,
        username: str,
        password: str,
        session: ClientSession,
    ) -> None:
        self._username = username
        self._password = password
        self._session = session
        self._access_token: str | None = None
        self._refresh_token: str | None = None
        self._auth_lock = asyncio.Lock()

    def is_authenticated(self) -> bool:
        """Return True if a valid access token is available."""
        return self._access_token is not None

    def _url(self, endpoint: str) -> str:
        """Return full API URL."""
        return endpoint

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------

    async def _login(self) -> None:
        """Authenticate and obtain tokens."""
        payload = {"user": self._username, "password": self._password}

        _LOGGER.debug("Attempting login to PWN API")
        _LOGGER.debug("Login endpoint: %s", self._url(AUTH_ENDPOINT))
        _LOGGER.debug("Payload user: %s", self._username)

        try:
            async with asyncio.timeout(API_TIMEOUT):
                async with self._session.post(
                    self._url(AUTH_ENDPOINT), json=payload
                ) as resp:
                    await self._raise_for_status(resp)

                    data: dict[str, object] = await resp.json()
                    access_token = data.get("accessToken")
                    refresh_token = data.get("refreshToken")

                    _LOGGER.debug(
                        "Received access token: %s", "SET" if access_token else "NONE"
                    )
                    _LOGGER.debug(
                        "Received refresh token: %s", "SET" if refresh_token else "NONE"
                    )

                    if not isinstance(access_token, str):
                        raise ConfigEntryAuthFailed("Authentication failed")

                    self._access_token = access_token
                    self._refresh_token = (
                        refresh_token if isinstance(refresh_token, str) else None
                    )

                    _LOGGER.info("Login successful for user %s", self._username)

        except asyncio.TimeoutError as exc:
            raise UpdateFailed("Login timeout") from exc
        except ClientError as exc:
            raise UpdateFailed("Login communication error") from exc

    async def _refresh_tokens(self) -> None:
        """Refresh authentication tokens."""
        payload = {
            "user": self._username,
            "password": self._password,
            "refresh_token": self._refresh_token,
        }

        async with asyncio.timeout(API_TIMEOUT):
            async with self._session.post(
                self._url(AUTH_ENDPOINT),
                json=payload,
            ) as resp:
                if resp.status in (401, 403):
                    raise ConfigEntryAuthFailed("Authentication expired")

                await self._raise_for_status(resp)

                data = await resp.json()

                access_token = data.get("accessToken")
                refresh_token = data.get("refreshToken")

                if not isinstance(access_token, str):
                    raise ConfigEntryAuthFailed("Token refresh failed")

                self._access_token = access_token
                self._refresh_token = (
                    refresh_token if isinstance(refresh_token, str) else None
                )

    @overload
    async def _request(
        self, method: str, endpoint: str, *, expect_list: Literal[True], **kwargs
    ) -> list[dict[str, object]]: ...

    @overload
    async def _request(
        self,
        method: str,
        endpoint: str,
        *,
        expect_list: Literal[False] = False,
        **kwargs,
    ) -> dict[str, object]: ...

    @overload
    async def _request(
        self, method: str, endpoint: str, *, expect_list: None, **kwargs
    ) -> dict[str, object] | list[dict[str, object]]: ...

    async def _request(
        self,
        method: str,
        endpoint: str,
        *,
        expect_list: bool | None = False,
        expected_status: int | None = None,
        **kwargs,
    ) -> dict[str, object] | list[dict[str, object]]:
        """Perform an authenticated HTTP request with a validated JSON shape."""
        if self._access_token is None:
            async with self._auth_lock:
                if self._access_token is None:
                    await self._login()

        headers = dict(kwargs.pop("headers", {}))
        headers["Authorization"] = f"Bearer {self._access_token}"

        try:
            async with asyncio.timeout(API_TIMEOUT):
                async with self._session.request(
                    method,
                    self._url(endpoint),
                    headers=headers,
                    **kwargs,
                ) as resp:
                    if resp.status in (401, 403):
                        _LOGGER.debug("Token expired, refreshing")
                        async with self._auth_lock:
                            # await self._login()
                            await self._refresh_tokens()

                        headers["Authorization"] = f"Bearer {self._access_token}"

                        async with self._session.request(
                            method,
                            self._url(endpoint),
                            headers=headers,
                            **kwargs,
                        ) as retry_resp:
                            await self._raise_for_status(retry_resp)
                            self._check_expected_status(
                                retry_resp.status, expected_status
                            )
                            return self._parse_json_response(
                                await retry_resp.json(), expect_list=expect_list
                            )

                    await self._raise_for_status(resp)
                    self._check_expected_status(resp.status, expected_status)
                    return self._parse_json_response(
                        await resp.json(), expect_list=expect_list
                    )

        except asyncio.TimeoutError as exc:
            raise UpdateFailed("Timeout communicating with API") from exc
        except ClientError as exc:
            raise UpdateFailed("Network error communicating with API") from exc

    @staticmethod
    def _check_expected_status(status_code: int, expected_status: int | None) -> None:
        """Reject unexpected successful statuses before parsing protected data."""
        if expected_status is not None and status_code != expected_status:
            raise UpdateFailed("Unexpected HTTP status from PWN API")

    @staticmethod
    def _parse_json_response(
        payload: object, *, expect_list: bool | None
    ) -> dict[str, object] | list[dict[str, object]]:
        """Validate JSON shapes while allowing optional notification arrays."""
        if expect_list is True or (expect_list is None and isinstance(payload, list)):
            if not isinstance(payload, list) or not all(
                isinstance(item, dict) for item in payload
            ):
                raise PWNAPIError("Invalid PWN API response: expected an array")
            return payload

        if not isinstance(payload, dict):
            raise PWNAPIError("Invalid PWN API response: expected an object")
        return payload

    @staticmethod
    def build_private_confirmation_url(relation_number: str) -> str:
        """Build the 10-digit identifier from three zeroes and seven relation digits.

        This function performs no network request. The confirmation token is
        returned as additionalDataToken by measurement request validation.
        Private-information submission is not implemented.
        """
        if (
            not isinstance(relation_number, str)
            or len(relation_number) != 7
            or not relation_number.isascii()
            or not relation_number.isdecimal()
        ):
            raise ValueError(
                "Invalid relation number: expected exactly seven ASCII digits"
            )

        return (
            f"{DATA_ENDPOINTS['supplement_information_private']}/000{relation_number}"
        )

    @staticmethod
    def build_plausible_reading_url(reading_number: str, relation_number: str) -> str:
        """Build the plausibility-check URL without sending any meter readings.

        The observed URL contains an 11-digit opname number and a 10-digit
        relation identifier made from three zeroes and seven relation digits.
        The JSON request body and three response fields are confirmed.
        """
        if (
            not isinstance(reading_number, str)
            or len(reading_number) != 11
            or not reading_number.isascii()
            or not reading_number.isdecimal()
        ):
            raise ValueError("Invalid reading number: expected exactly 11 ASCII digits")
        if (
            not isinstance(relation_number, str)
            or len(relation_number) != 7
            or not relation_number.isascii()
            or not relation_number.isdecimal()
        ):
            raise ValueError(
                "Invalid relation number: expected exactly seven ASCII digits"
            )

        return (
            f"{DATA_ENDPOINTS['water_usage_validate_plausible_reading']}"
            f"/{reading_number}/000{relation_number}"
        )

    @staticmethod
    def _build_plausible_reading_payload(
        meter_reading: int,
        reading_date: date,
        address: dict[str, object],
        wheel_count: int,
    ) -> PWNReadingPlausibilityRequest:
        """Validate the confirmed plausibility request body without a POST.

        Reading dates are calendar dates, not submission timestamps. The
        numeric JSON reading has no padding, even for multi-wheel meters.
        """
        if type(wheel_count) is not int or not 1 <= wheel_count <= 9:
            raise ValueError("Invalid meter wheel count: expected 1 to 9 digits")
        if type(meter_reading) is not int or not 0 <= meter_reading < 10**wheel_count:
            raise ValueError(
                "Invalid meter reading: expected a whole number within the wheel range"
            )
        if type(reading_date) is not date:
            raise ValueError("Invalid reading date: expected a calendar date")
        if not isinstance(address, dict):
            raise ValueError("Invalid plausibility address: expected an object")

        validated: dict[str, str] = {}
        for key in ("postalCode", "houseNumber", "houseNumberAddition"):
            value = address.get(key)
            if (
                not isinstance(value, str)
                or len(value) > 255
                or (key != "houseNumberAddition" and not value.strip())
            ):
                raise ValueError(
                    "Invalid plausibility address: missing or invalid fields"
                )
            validated[key] = value

        return PWNReadingPlausibilityRequest(
            meterReading=meter_reading,
            readingDate=reading_date.isoformat(),
            postalCode=validated["postalCode"],
            houseNumber=validated["houseNumber"],
            houseNumberAddition=validated["houseNumberAddition"],
        )

    @staticmethod
    def parse_plausible_reading_response(
        payload: object,
    ) -> PWNReadingPlausibilityResponse:
        """Validate plausibility flags and nullable bounds without side effects.

        Non-null numeric bounds are accepted defensively, but their exact
        server-side behavior has not yet been observed.
        """
        if not isinstance(payload, dict):
            raise PWNAPIError("Invalid plausibility response: expected an object")

        plausible = payload.get("isPlausible")
        if type(plausible) is not bool:
            raise PWNAPIError("Invalid plausibility response: expected a boolean")

        bounds: dict[str, int | float | None] = {}
        for key in ("minReading", "maxReading"):
            if key not in payload:
                raise PWNAPIError("Invalid plausibility response: missing bound field")
            value = payload[key]
            if value is None:
                bounds[key] = None
            elif type(value) is int and value >= 0:
                bounds[key] = value
            elif type(value) is float and isfinite(value) and value >= 0:
                bounds[key] = value
            else:
                raise PWNAPIError(
                    "Invalid plausibility response: invalid reading bound"
                )

        minimum = bounds["minReading"]
        maximum = bounds["maxReading"]
        if minimum is not None and maximum is not None and minimum > maximum:
            raise PWNAPIError("Invalid plausibility response: inconsistent bounds")

        return PWNReadingPlausibilityResponse(
            isPlausible=plausible,
            minReading=minimum,
            maxReading=maximum,
        )

    @staticmethod
    def _build_final_reading_payload(
        meter_reading: int,
        reading_date: date,
        address: dict[str, object],
        wheel_count: int,
    ) -> PWNFinalReadingRequest:
        """Validate and build the exact final PWN submission request body."""
        validated = PWNAPI._build_plausible_reading_payload(
            meter_reading, reading_date, address, wheel_count
        )
        street = address.get("street")
        if not isinstance(street, str) or not street.strip() or len(street) > 255:
            raise ValueError("Invalid meter address: missing or invalid street")

        return PWNFinalReadingRequest(
            MeterReading=validated["meterReading"],
            AnomalyReason=None,
            ReadingDate=validated["readingDate"],
            PostalCode=validated["postalCode"],
            Street=street,
            HouseNumber=validated["houseNumber"],
            HouseNumberAddition=validated["houseNumberAddition"],
            WaterMeterImage=None,
        )

    @staticmethod
    def parse_final_reading_response(payload: object) -> PWNFinalReadingResponse:
        """Accept only an unambiguous successful PWN final submission response."""
        if not isinstance(payload, dict):
            raise PWNAPIError(
                "Invalid final meter reading response: expected an object"
            )

        success = payload.get("success")
        error_text = payload.get("errorText")
        if type(success) is not bool or not isinstance(error_text, str):
            raise PWNAPIError("Invalid final meter reading response: missing fields")
        if not success:
            raise PWNReadingSubmissionRejected(
                "PWN rejected the meter reading; the previous confirmed state is unchanged"
            )
        if error_text:
            raise PWNAPIError(
                "Invalid final meter reading response: inconsistent fields"
            )

        return PWNFinalReadingResponse(success=True, errorText="")

    @staticmethod
    def _build_measurement_validation_payload(
        address: dict[str, object],
    ) -> PWNMeasurementValidationRequest:
        """Extract only the confirmed address fields for validation."""
        if not isinstance(address, dict):
            raise ValueError(
                "Invalid measurement validation address: expected an object"
            )

        validated: dict[str, str] = {}
        for key in ("houseNumber", "houseNumberAddition", "postalCode"):
            value = address.get(key)
            if (
                not isinstance(value, str)
                or len(value) > 255
                or (key != "houseNumberAddition" and not value.strip())
            ):
                raise ValueError(
                    "Invalid measurement validation address: missing or invalid fields"
                )
            validated[key] = value

        return PWNMeasurementValidationRequest(
            houseNumber=validated["houseNumber"],
            houseNumberAddition=validated["houseNumberAddition"],
            postalCode=validated["postalCode"],
        )

    @staticmethod
    def parse_measurement_validation(
        payload: object,
    ) -> PWNMeasurementValidationResponse:
        """Validate a measurement response without logging the returned token."""
        if not isinstance(payload, dict):
            raise PWNAPIError(
                "Invalid measurement validation response: expected an object"
            )

        for key in ("relationNumber", "additionalDataToken"):
            value = payload.get(key)
            if not isinstance(value, str) or not value:
                raise PWNAPIError(
                    "Invalid measurement validation response: missing fields"
                )

        relation_number = payload["relationNumber"]
        if (
            not relation_number.isascii()
            or not relation_number.isdecimal()
            or len(relation_number) > 32
        ):
            raise PWNAPIError(
                "Invalid measurement validation response: relation number"
            )

        for key in ("clientType", "readingReason"):
            value = payload.get(key)
            if type(value) is not int or value < 0:
                raise PWNAPIError(
                    "Invalid measurement validation response: status code"
                )

        addition = payload.get("houseNumberAddition")
        if not isinstance(addition, str):
            raise PWNAPIError(
                "Invalid measurement validation response: address addition"
            )

        for key in ("periodStartDate", "periodEndDate"):
            date_value = payload.get(key)
            if date_value is not None and (
                not isinstance(date_value, str) or not date_value
            ):
                raise PWNAPIError(
                    "Invalid measurement validation response: period date"
                )

        return PWNMeasurementValidationResponse(
            relationNumber=relation_number,
            clientType=payload["clientType"],
            additionalDataToken=payload["additionalDataToken"],
            readingReason=payload["readingReason"],
            periodStartDate=payload.get("periodStartDate"),
            periodEndDate=payload.get("periodEndDate"),
            houseNumberAddition=addition,
        )

    @staticmethod
    def _build_water_meter_details_payload(
        address: dict[str, object],
    ) -> PWNWaterMeterDetailsRequest:
        """Validate address fields and omit unrelated account information."""
        if not isinstance(address, dict):
            raise ValueError("Invalid water meter address: expected an object")

        address_keys = (
            "postalCode",
            "street",
            "houseNumber",
            "houseNumberAddition",
        )
        validated: dict[str, str] = {}
        for key in address_keys:
            value = address.get(key)
            if (
                not isinstance(value, str)
                or len(value) > 255
                or (key != "houseNumberAddition" and not value.strip())
            ):
                raise ValueError(
                    "Invalid water meter address: missing or invalid fields"
                )
            validated[key] = value

        return PWNWaterMeterDetailsRequest(
            postalCode=validated["postalCode"],
            street=validated["street"],
            houseNumber=validated["houseNumber"],
            houseNumberAddition=validated["houseNumberAddition"],
        )

    @staticmethod
    def parse_water_meter_details(payload: object) -> PWNWaterMeterDetails:
        """Validate the WaterUsage meter-details response without network access.

        Empty meter types and zero red wheels are valid response values.
        """
        if not isinstance(payload, dict):
            raise PWNAPIError("Invalid water meter details: expected an object")

        meter_type = payload.get("meterType")
        black_wheels = payload.get("amountOfBlackWheels")
        red_wheels = payload.get("amountOfRedWheels")
        if not isinstance(meter_type, str):
            raise PWNAPIError("Invalid water meter details: meter type")
        if type(black_wheels) is not int or black_wheels < 0:
            raise PWNAPIError("Invalid water meter details: black wheel count")
        if type(red_wheels) is not int or red_wheels < 0:
            raise PWNAPIError("Invalid water meter details: red wheel count")

        return PWNWaterMeterDetails(
            meterType=meter_type,
            amountOfBlackWheels=black_wheels,
            amountOfRedWheels=red_wheels,
        )

    @staticmethod
    def parse_move_house_address(payload: object) -> PWNMoveHouseAddress:
        """Validate and type a MoveHouse address response without logging PII.

        The endpoint's HTTP method and request payload are still unknown.
        This parser does not send any network requests or change an account.
        """
        if not isinstance(payload, dict):
            raise PWNAPIError("Invalid MoveHouse address response: expected an object")

        fields = (
            "street",
            "houseNumber",
            "houseNumberAddition",
            "postalCode",
            "city",
            "country",
        )
        values: dict[str, str] = {}
        for field_name in fields:
            field_value = payload.get(field_name)
            if not isinstance(field_value, str) or (
                field_name != "houseNumberAddition" and not field_value.strip()
            ):
                raise PWNAPIError("Invalid MoveHouse address response: missing fields")
            values[field_name] = field_value

        return PWNMoveHouseAddress(
            street=values["street"],
            houseNumber=values["houseNumber"],
            houseNumberAddition=values["houseNumberAddition"],
            postalCode=values["postalCode"],
            city=values["city"],
            country=values["country"],
        )

    # ------------------------------------------------------------------
    # Public API methods
    # ------------------------------------------------------------------
    async def get_invoices(self) -> dict[str, object]:
        """Return invoices summary."""
        return await self._request("GET", DATA_ENDPOINTS["invoices_summary"])

    async def get_relation_data(self) -> dict[str, object]:
        """Return user relation data."""
        return await self._request("GET", DATA_ENDPOINTS["relation_data"])

    async def get_consumption_history(self) -> dict[str, object]:
        """Return consumption history."""
        return await self._request("GET", DATA_ENDPOINTS["consumption_history"])

    async def get_water_meter_details(
        self, address: dict[str, object]
    ) -> PWNWaterMeterDetails:
        """Retrieve meter dial configuration for an explicitly supplied address.

        This POST is not called by the coordinator or during setup.
        """
        payload = self._build_water_meter_details_payload(address)
        try:
            response = await self._request(
                "POST", DATA_ENDPOINTS["water_usage_meter_details"], json=payload
            )
        except UpdateFailed:
            # Upstream error bodies can contain personally identifying addresses.
            raise UpdateFailed("Unable to retrieve water meter details") from None

        return self.parse_water_meter_details(response)

    async def validate_measurement_request(
        self, reading_number: str, address: dict[str, object]
    ) -> PWNMeasurementValidationResponse:
        """Request a validation token for a reading number and an address.

        The token is returned only to the caller; it is never persisted or logged.
        The method is not called automatically during Home Assistant setup.
        """
        if (
            not isinstance(reading_number, str)
            or len(reading_number) != 11
            or not reading_number.isascii()
            or not reading_number.isdecimal()
        ):
            raise ValueError("Invalid reading number: expected exactly 11 ASCII digits")
        payload = self._build_measurement_validation_payload(address)
        endpoint = (
            f"{DATA_ENDPOINTS['water_usage_validate_measurement_request']}"
            f"/{reading_number}"
        )
        try:
            response = await self._request("POST", endpoint, json=payload)
        except UpdateFailed:
            raise UpdateFailed("Unable to validate meter-reading request") from None

        return self.parse_measurement_validation(response)

    async def validate_plausible_reading(
        self,
        reading_number: str,
        relation_number: str,
        meter_reading: int,
        reading_date: date,
        address: dict[str, object],
        wheel_count: int,
    ) -> PWNReadingPlausibilityResponse:
        """Check a draft for plausibility without recording a submitted reading.

        This method is opt-in and is not called by setup, polling, or entities.
        A false flag raises PWNReadingNotPlausibleError and blocks callers
        from progressing to final submission. A true flag is not proof
        that the reading has been submitted.
        """
        endpoint = self.build_plausible_reading_url(reading_number, relation_number)
        payload = self._build_plausible_reading_payload(
            meter_reading, reading_date, address, wheel_count
        )
        try:
            response = await self._request("POST", endpoint, json=payload)
        except UpdateFailed:
            raise UpdateFailed(
                "Unable to validate meter-reading plausibility"
            ) from None

        result = self.parse_plausible_reading_response(response)
        if not result["isPlausible"]:
            raise PWNReadingNotPlausibleError(
                result["minReading"], result["maxReading"]
            )
        return result

    async def submit_meter_reading(
        self,
        reading_number: str,
        meter_reading: int,
        reading_date: date,
        address: dict[str, object],
        wheel_count: int,
    ) -> PWNFinalReadingResponse:
        """Submit only an explicitly requested reading to the final PWN API.

        Call this only after a successful plausibility check. HTTP 200 plus a
        JSON success=true/errorText="" acknowledgement is required.
        """
        if (
            not isinstance(reading_number, str)
            or len(reading_number) != 11
            or not reading_number.isascii()
            or not reading_number.isdecimal()
        ):
            raise ValueError("Invalid reading number: expected exactly 11 ASCII digits")

        payload = self._build_final_reading_payload(
            meter_reading, reading_date, address, wheel_count
        )
        endpoint = f"{DATA_ENDPOINTS['water_usage_submit_reading']}/{reading_number}"
        try:
            result = await self._request(
                "POST", endpoint, json=payload, expected_status=200
            )
        except UpdateFailed:
            raise UpdateFailed("Unable to submit meter reading to PWN") from None

        return self.parse_final_reading_response(result)

    async def get_reading_request(self) -> list[dict[str, object]]:
        """Retain all meter requests, isolating malformed metadata per meter."""
        try:
            return await self._request(
                "GET", DATA_ENDPOINTS["reading_request"], expect_list=True
            )
        except UpdateFailed:
            raise UpdateFailed("Unable to retrieve meter-reading requests") from None

    async def get_payment_info(self) -> dict[str, object]:
        """Return payment information."""
        return await self._request("GET", DATA_ENDPOINTS["payment_info"])

    async def get_advance(self) -> dict[str, object]:
        """Return advance information."""
        return await self._request("GET", DATA_ENDPOINTS["advance"])

    async def get_notifications(
        self,
    ) -> dict[str, object] | list[dict[str, object]]:
        """Preserve object and array notifications without assuming a schema."""
        return await self._request(
            "GET", DATA_ENDPOINTS["notifications"], expect_list=None
        )

    async def get_contact_preferences(self, customer_id: str) -> dict[str, object]:
        """Return contact preferences for a given customer ID."""
        url = f"{DATA_ENDPOINTS['contact_preferences']}/{customer_id}"
        return await self._request("GET", url)

    async def get_settings(self) -> dict[str, object]:
        """Return settings."""
        return await self._request("GET", DATA_ENDPOINTS["settings"])

    async def get_takeover(self) -> dict[str, object]:
        """Return takeover information."""
        return await self._request("GET", DATA_ENDPOINTS["takeover"])

    # ------------------------------------------------------------------
    # Response helper
    # ------------------------------------------------------------------

    async def _raise_for_status(self, resp: ClientResponse) -> None:
        """Map HTTP status codes to HA exceptions."""
        if resp.status < 400:
            return

        if resp.status in (401, 403):
            raise ConfigEntryAuthFailed("Authentication expired")

        if 400 <= resp.status < 500:
            text = await resp.text()
            raise UpdateFailed(f"Client error {resp.status}: {text}")

        if resp.status >= 500:
            text = await resp.text()
            raise UpdateFailed(f"Server error {resp.status}: {text}")
