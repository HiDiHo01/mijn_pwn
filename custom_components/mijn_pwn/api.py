import asyncio
import logging
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


class PWNAPIServerError(PWNAPIError):
    """Server-side API error."""


class PWNAPICommunicationError(PWNAPIError):
    """Communication failure with the API."""


class PWNAPIAuthenticationError(PWNAPIError):
    """Authentication error."""


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

    async def _request(
        self, method: str, endpoint: str, *, expect_list: bool = False, **kwargs
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
                            return self._parse_json_response(
                                await retry_resp.json(), expect_list=expect_list
                            )

                    await self._raise_for_status(resp)
                    return self._parse_json_response(
                        await resp.json(), expect_list=expect_list
                    )

        except asyncio.TimeoutError as exc:
            raise UpdateFailed("Timeout communicating with API") from exc
        except ClientError as exc:
            raise UpdateFailed("Network error communicating with API") from exc

    @staticmethod
    def _parse_json_response(
        payload: object, *, expect_list: bool
    ) -> dict[str, object] | list[dict[str, object]]:
        """Validate the JSON container without exposing account response data."""
        if expect_list:
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

        This function performs no network request. The confirmation token source
        remains unknown, so submitting private information is not implemented.
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

    async def get_reading_request(self) -> list[dict[str, object]]:
        """Return meter-reading requests with their status and meter metadata."""
        requests = await self._request(
            "GET", DATA_ENDPOINTS["reading_request"], expect_list=True
        )
        for request in requests:
            if not isinstance(request.get("openReading"), bool):
                raise PWNAPIError("Invalid meter-reading request: missing open status")
            if request["openReading"]:
                reading_number = request.get("readingNumber")
                wheel_count = request.get("amountOfWheels")
                if not isinstance(reading_number, str) or not reading_number:
                    raise PWNAPIError("Invalid open meter-reading request: number")
                if type(wheel_count) is not int or wheel_count <= 0:
                    raise PWNAPIError("Invalid open meter-reading request: wheel count")
        return requests

    async def get_payment_info(self) -> dict[str, object]:
        """Return payment information."""
        return await self._request("GET", DATA_ENDPOINTS["payment_info"])

    async def get_advance(self) -> dict[str, object]:
        """Return advance information."""
        return await self._request("GET", DATA_ENDPOINTS["advance"])

    async def get_notifications(self) -> dict[str, object]:
        """Return notifications."""
        return await self._request("GET", DATA_ENDPOINTS["notifications"])

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
