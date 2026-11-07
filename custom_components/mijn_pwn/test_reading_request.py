"""Regression tests for the Mijn PWN meter-reading request API."""

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from .api import PWNAPI
from .const import API_URL, DATA_ENDPOINTS


class TestReadingRequest(unittest.IsolatedAsyncioTestCase):
    """Verify the reading-request endpoint without accessing a real account."""

    def test_full_reading_request_endpoint(self) -> None:
        """Keep the complete reading-request URL and its false flag unchanged."""
        self.assertEqual(
            DATA_ENDPOINTS["reading_request"],
            f"{API_URL}/api/connection/reading-request?withReadingNumberOnly=false",
        )

    async def test_get_reading_request_uses_authenticated_client(self) -> None:
        """Route the read-only request through the existing authenticated client."""
        api = PWNAPI(
            username="synthetic@example.invalid",
            password="synthetic-password",
            session=MagicMock(),
        )
        response_data = {"readingRequest": "synthetic"}

        with patch.object(api, "_request", new_callable=AsyncMock) as request_mock:
            request_mock.return_value = response_data
            result = await api.get_reading_request()

        self.assertEqual(result, response_data)
        request_mock.assert_awaited_once_with("GET", DATA_ENDPOINTS["reading_request"])
