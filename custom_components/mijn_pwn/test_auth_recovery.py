"""Offline checks for authentication propagation and Home Assistant recovery."""

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from aiohttp import ClientConnectionError

from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.update_coordinator import UpdateFailed

from . import _create_coordinator, async_setup_entry
from .api import PWNAPI, PWNAPIAuthenticationError
from .const import DOMAIN
from .coordinator import PWNDataUpdateCoordinator


def _mock_coordinator() -> MagicMock:
    """Build seven independent fake API endpoints without network access."""
    coordinator = MagicMock()
    api = coordinator.api
    api.get_invoices = AsyncMock(return_value=[{"number": "synthetic"}])
    api.get_relation_data = AsyncMock(return_value={"relationNumber": "synthetic"})
    api.get_consumption_history = AsyncMock(return_value=[{"amount": 1}])
    api.get_reading_request = AsyncMock(return_value=[])
    api.get_payment_info = AsyncMock(return_value={})
    api.get_advance = AsyncMock(return_value={})
    api.get_notifications = AsyncMock(return_value={})
    api.get_contact_preferences = AsyncMock(return_value={"enabled": True})
    return coordinator


def _mock_response_context(status_code: int) -> MagicMock:
    """Build a synthetic aiohttp response context for the requested status."""
    response = MagicMock()
    response.status = status_code
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=response)
    context.__aexit__ = AsyncMock(return_value=None)
    return context


class TestCoordinatorAuthRecovery(unittest.IsolatedAsyncioTestCase):
    """Do not convert authentication failures into unavailable optional data."""

    async def test_parallel_auth_failure_triggers_reauth(self) -> None:
        """Any endpoint authentication failure must not be silently isolated."""
        for exception_class in (
            ConfigEntryAuthFailed,
            PWNAPIAuthenticationError,
        ):
            with self.subTest(exception_class=exception_class.__name__):
                coordinator = _mock_coordinator()
                coordinator.api.get_notifications.side_effect = exception_class(
                    "synthetic-private-response-not-for-logs"
                )

                with self.assertRaises(ConfigEntryAuthFailed) as result:
                    await PWNDataUpdateCoordinator._fetch_all_data(coordinator)

                self.assertNotIn(
                    "synthetic-private-response",
                    str(result.exception),
                )

    async def test_required_endpoint_auth_error_triggers_reauth(self) -> None:
        """Even a required endpoint cannot hide invalid credentials."""
        coordinator = _mock_coordinator()
        coordinator.api.get_relation_data.side_effect = ConfigEntryAuthFailed(
            "fictional-authentication-failure"
        )
        with self.assertRaises(ConfigEntryAuthFailed):
            await PWNDataUpdateCoordinator._fetch_all_data(coordinator)

    async def test_transient_endpoint_error_does_not_trigger_reauth(self) -> None:
        """Preserve valid endpoint data when an optional request times out."""
        coordinator = _mock_coordinator()
        coordinator.api.get_notifications.side_effect = UpdateFailed(
            "synthetic-transient-error"
        )

        result = await PWNDataUpdateCoordinator._fetch_all_data(coordinator)

        self.assertIsNone(result["notifications"])
        self.assertEqual(result["user_data"], {"relationNumber": "synthetic"})
        self.assertEqual(result["contact_preferences"], {"enabled": True})

    async def test_contact_preferences_auth_error_is_propagated(self) -> None:
        """Late optional API requests must also raise a reauthentication error."""
        coordinator = _mock_coordinator()
        coordinator.api.get_contact_preferences.side_effect = ConfigEntryAuthFailed(
            "fictional-expired-session"
        )

        with self.assertRaises(ConfigEntryAuthFailed):
            await PWNDataUpdateCoordinator._fetch_all_data(coordinator)

    async def test_contact_preferences_transient_error_is_isolated(self) -> None:
        """A networking failure in contact preferences is not bad credentials."""
        coordinator = _mock_coordinator()
        coordinator.api.get_contact_preferences.side_effect = UpdateFailed(
            "synthetic-timeout"
        )

        data = await PWNDataUpdateCoordinator._fetch_all_data(coordinator)

        self.assertIsNone(data["contact_preferences"])
        self.assertTrue(data["invoices"])

    async def test_update_data_propagates_auth_failure(self) -> None:
        """Ensure a coordinator refresh does not wrap auth as UpdateFailed."""
        coordinator = _mock_coordinator()
        coordinator._fetch_all_data = AsyncMock(
            side_effect=ConfigEntryAuthFailed("invalid-synthetic-session")
        )

        with self.assertRaises(ConfigEntryAuthFailed):
            await PWNDataUpdateCoordinator._async_update_data(coordinator)

    async def test_parallel_cancellation_is_not_swallowed(self) -> None:
        """Cancelled refreshes must propagate rather than writing partial data."""
        coordinator = _mock_coordinator()
        coordinator.api.get_invoices.side_effect = asyncio.CancelledError()

        with self.assertRaises(asyncio.CancelledError):
            await PWNDataUpdateCoordinator._fetch_all_data(coordinator)


class TestAPIAuthenticationErrors(unittest.IsolatedAsyncioTestCase):
    """Prove 401/403 require reauth but transport failures do not."""

    def setUp(self) -> None:
        """Create an HTTP client with only fictional credentials."""
        self._session = MagicMock()
        self._api = PWNAPI(
            username="synthetic@example.invalid",
            password="synthetic-password",
            session=self._session,
        )

    async def test_login_401_and_403_are_auth_errors(self) -> None:
        """Reject both invalid-credential HTTP statuses during login."""
        for status in (401, 403):
            with self.subTest(status=status):
                self._session.post.return_value = _mock_response_context(status)

                with self.assertRaises(ConfigEntryAuthFailed):
                    await self._api._login()

    async def test_refresh_401_and_403_are_auth_errors(self) -> None:
        """Expired refresh credentials cannot become a transient server error."""
        for status in (401, 403):
            with self.subTest(status=status):
                self._session.post.return_value = _mock_response_context(status)

                with self.assertRaises(ConfigEntryAuthFailed):
                    await self._api._refresh_tokens()

    async def test_protected_request_passes_auth_error_to_coordinator(self) -> None:
        """Protected endpoint unauthorized responses require fresh credentials."""
        self._api._access_token = "fictional-access-token"
        self._api._refresh_tokens = AsyncMock(
            side_effect=ConfigEntryAuthFailed("expired")
        )
        self._session.request.return_value = _mock_response_context(401)

        with self.assertRaises(ConfigEntryAuthFailed):
            await self._api._request("GET", "https://example.invalid/resource")

    async def test_transient_transport_failure_raises_update_failed(self) -> None:
        """A connection error must not start a reauthentication flow."""
        self._api._access_token = "fictional-access-token"
        context = _mock_response_context(200)
        context.__aenter__.side_effect = ClientConnectionError("synthetic-offline")
        self._session.request.return_value = context

        with self.assertRaises(UpdateFailed):
            await self._api._request("GET", "https://example.invalid/resource")


class TestSetupAuthenticationRecovery(unittest.IsolatedAsyncioTestCase):
    """Initial setup must let HA manage reauth or schedule retry."""

    def setUp(self) -> None:
        """Create an account beside an unrelated, already loaded account."""
        self._hass = MagicMock()
        self._hass.data = {DOMAIN: {"other-entry": "other-account-coordinator"}}
        self._entry = MagicMock()
        self._entry.entry_id = "fictitious-current-entry"
        self._entry.unique_id = "synthetic@example.invalid"
        self._entry.data = {
            "username": self._entry.unique_id,
            "password": "synthetic-password",
        }

    async def test_first_refresh_auth_error_propagates_without_stale_entry(
        self,
    ) -> None:
        """Leave another account untouched if initial auth cannot succeed."""
        coordinator = MagicMock()
        coordinator.async_load_confirmed_readings = AsyncMock()
        coordinator.async_config_entry_first_refresh = AsyncMock(
            side_effect=ConfigEntryAuthFailed("fictional-expired-session")
        )

        with patch(
            "custom_components.mijn_pwn._create_coordinator",
            new=AsyncMock(return_value=coordinator),
        ):
            with self.assertRaises(ConfigEntryAuthFailed):
                await async_setup_entry(self._hass, self._entry)

        self.assertEqual(
            self._hass.data[DOMAIN],
            {"other-entry": "other-account-coordinator"},
        )
        coordinator.async_load_drafts.assert_not_awaited()

    async def test_first_refresh_temporary_failure_remains_retryable(self) -> None:
        """Do not misclassify a temporary setup failure as invalid credentials."""
        coordinator = MagicMock()
        coordinator.async_load_confirmed_readings = AsyncMock()
        coordinator.async_config_entry_first_refresh = AsyncMock(
            side_effect=ConfigEntryNotReady("synthetic-network-failure")
        )

        with patch(
            "custom_components.mijn_pwn._create_coordinator",
            new=AsyncMock(return_value=coordinator),
        ):
            with self.assertRaises(ConfigEntryNotReady):
                await async_setup_entry(self._hass, self._entry)

        self.assertEqual(
            self._hass.data[DOMAIN],
            {"other-entry": "other-account-coordinator"},
        )

    async def test_missing_credentials_require_reauthentication(self) -> None:
        """Missing saved credentials cannot recover by retrying automatically."""
        with patch("custom_components.mijn_pwn._get_credentials", return_value=None):
            with self.assertRaises(ConfigEntryAuthFailed):
                await _create_coordinator(self._hass, self._entry)
