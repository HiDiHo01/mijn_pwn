import asyncio
import logging
from typing import Any, Optional

import async_timeout
from aiohttp import ClientError, ClientSession
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed

from .const import API_ENDPOINTS, API_TIMEOUT, API_URL

_LOGGER = logging.getLogger(__name__)


class PWNAPIError(Exception):
    """Exception to indicate a general API error."""


class PWNAPICommunicationError(PWNAPIError):
    """Exception to indicate a communication error."""


class PWNAPIAuthenticationError(PWNAPIError):
    """Exception to indicate an authentication error."""


class PWNAuth:
    """Handles the authentication for Mijn PWN"""

    def __init__(self, username: str, password: str):
        """
        Initialize the PWNAuth class with username and password.

        Args:
            username (str): The username for authentication.
            password (str): The password for authentication.
        """
        self.username = username
        self.password = password
        self.access_token: Optional[str] = None
        self.refresh_token: Optional[str] = None

    async def login(self, session: ClientSession) -> bool:
        """
        Perform login and retrieve access and refresh tokens.

        Args:
            session (ClientSession): The aiohttp session for making requests.

        Returns:
            bool: True if login was successful, False otherwise.
        """
        payload = {
            'user': self.username,
            'password': self.password
        }

        try:
            with async_timeout.timeout(API_TIMEOUT):
                async with session.post(API_ENDPOINTS['login'], json=payload) as response:
                    if response.status == 200:
                        tokens = await response.json()
                        self.access_token = tokens.get('accessToken')
                        self.refresh_token = tokens.get('refreshToken')
                        _LOGGER.info("Auth login successful")
                        return True
                    elif response.status in (401, 403):
                        _LOGGER.error("Invalid credentials")
                    else:
                        _LOGGER.error(
                            "Failed to login. Status code: %s", response.status)
                    return False
        except asyncio.TimeoutError:
            _LOGGER.error("Timeout occurred during login")
            return False
        except ClientError as e:
            _LOGGER.error("Client error occurred during login: %s", e)
            return False
        except Exception as e:
            _LOGGER.error("Unexpected error occurred during login: %s", e)
            return False

    async def refresh_tokens(self, session: ClientSession):
        """
        Refresh the access and refresh tokens using the refresh token.

        Args:
            session (ClientSession): The aiohttp session for making requests.
        """
        if not self.refresh_token:
            _LOGGER.error("No refresh token available. Need to login first.")
            return

        payload = {
            'user': self.username,
            'password': self.password,
            'refresh_token': self.refresh_token
        }

        try:
            with async_timeout.timeout(API_TIMEOUT):
                async with session.post(API_ENDPOINTS['login'], json=payload) as response:
                    if response.status == 200:
                        tokens = await response.json()
                        self.access_token = tokens.get('accessToken')
                        self.refresh_token = tokens.get('refreshToken')
                        _LOGGER.info("Auth token refresh successful")
                    elif response.status in (401, 403):
                        _LOGGER.error(
                            "Refresh token expired or invalid, re-login required.")
                    else:
                        _LOGGER.error(
                            "Failed to refresh tokens. Status code: %s", response.status)
        except PWNAPIAuthenticationError as ex:
            _LOGGER.error(
                "Failed to renew token: %s. Starting user reauth flow", ex)
            raise ConfigEntryAuthFailed from ex
        except asyncio.TimeoutError:
            _LOGGER.error("Timeout occurred during token refresh")
        except ClientError as e:
            _LOGGER.error("Client error occurred during token refresh: %s", e)
        except Exception as e:
            _LOGGER.error(
                "Unexpected error occurred during token refresh: %s", e)

    def is_authenticated(self) -> bool:
        """
        Check if the user is authenticated.

        Returns:
            bool: True if the user is authenticated, False otherwise.
        """
        authenticated = bool(self.access_token)
        if authenticated:
            _LOGGER.debug("User is authenticated")
        return authenticated

    def logout(self):
        """
        Perform logout by clearing the tokens.
        """
        self.access_token = None
        self.refresh_token = None
        _LOGGER.info("Logout successful")


class PWNAPI:
    """Class to interact with the PWN API."""

    def __init__(self, auth: PWNAuth, session: ClientSession):
        """Initialize the API client."""
        self.auth = auth
        self.session = session

    async def async_get_data(self, endpoint: str) -> Optional[dict]:
        """Fetch data from API endpoint"""
        url = API_ENDPOINTS.get(endpoint)
        if not url:
            _LOGGER.error("Invalid endpoint: %s", endpoint)
            return None
        return await self.fetch_data_from_api(url)

    async def async_get_settings(self) -> Optional[dict]:
        """Retrieve settings from the API."""
        return await self.async_get_data(API_ENDPOINTS['settings'])

    async def fetch_data(self):
        """Fetch data from the API."""
        if not self.auth.is_authenticated():
            await self.auth.login(self.session)

        headers = {"Authorization": f"Bearer {self.auth.access_token}"}
        async with self.session.get(API_URL, headers=headers) as response:
            if response.status != 200:
                raise Exception(f"Error fetching data: {response.status}")
            return await response.json()

    async def fetch_data_from_api(self, url: str) -> dict[str, Any]:
        """Fetch data from API endpoint."""
        try:
            # Check if authenticated, if not, try to login
            if not self.auth.is_authenticated():
                await self.auth.login(self.session)

            # Set headers with the current access token
            headers = {"Authorization": f"Bearer {self.auth.access_token}"}

            # Fetch data from the API with a timeout
            with async_timeout.timeout(API_TIMEOUT):
                async with self.session.get(url, headers=headers, timeout=API_TIMEOUT) as response:
                    # Handle unauthorized access, attempt token refresh
                    if response.status in (401, 403):
                        _LOGGER.warning(
                            "Unauthorized access to %s. Refreshing token and retrying.", url)
                        await self.auth.refresh_tokens(self.session)

                        # Retry fetching the data with the refreshed token
                        headers = {"Authorization": f"Bearer {
                            self.auth.access_token}"}

                        with async_timeout.timeout(API_TIMEOUT):
                            async with self.session.get(url, headers=headers, timeout=API_TIMEOUT) as retry_response:
                                if retry_response.status != 200:
                                    _LOGGER.error(
                                        "Failed to fetch data from %s after token refresh. Status code: %s", url, retry_response.status)
                                    raise UpdateFailed(f"Error communicating with API: {
                                                       retry_response.status}")
                                data = await retry_response.json()
                                # _LOGGER.debug(
                                #     "Fetched data from %s after token refresh: %s", url, data)
                                _LOGGER.debug(
                                    "Fetched data from %s after token refresh", url)
                                return data

                    # Handle other response statuses
                    if response.status != 200:
                        _LOGGER.error(
                            "Failed to fetch data from %s. Status code: %s", url, response.status)
                        raise UpdateFailed(f"Error communicating with API: {
                                           response.status}")

                    # Successful response
                    data = await response.json()
                    # _LOGGER.debug("Fetched data from %s: %s", url, data)
                    _LOGGER.debug("Fetched data from %s: Successful", url)
                    return data
        except asyncio.TimeoutError as exception:
            raise PWNAPICommunicationError(
                "Timeout error fetching information") from exception
        except Exception as exception:
            _LOGGER.error(
                "Unexpected error fetching data from %s: %s", url, exception)
            raise PWNAPIError(
                "Something really wrong happened!") from exception
