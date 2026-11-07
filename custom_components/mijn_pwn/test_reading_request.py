"""Regression tests for the Mijn PWN meter-reading request API."""

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from .api import PWNAPI, PWNAPIError
from .const import API_URL, DATA_ENDPOINTS


class TestReadingRequest(unittest.IsolatedAsyncioTestCase):
    """Verify reading requests without sending data to a real account."""

    @staticmethod
    def _build_api() -> PWNAPI:
        """Create an API client using synthetic credentials."""
        return PWNAPI(
            username="synthetic@example.invalid",
            password="synthetic-password",
            session=MagicMock(),
        )

    @staticmethod
    def _reading_request() -> dict[str, object]:
        """Return a fictional request matching the observed API structure."""
        return {
            "openReading": True,
            "readingNumber": "00000000001",
            "readingStartDate": "2026-10-25T00:00:00.0000000Z",
            "readingDate": "2026-11-21T00:00:00.0000000Z",
            "readingReason": 1,
            "amountOfWheels": 5,
            "meterNumber": "00000000",
            "address": {
                "street": "Example Street",
                "houseNumber": "1",
                "houseNumberAddition": "",
                "postalCode": "0000 AA",
                "city": "Example City",
                "country": "NL",
            },
            "contractStatus": 1,
        }

    def test_full_reading_request_endpoint(self) -> None:
        """Keep the complete reading-request URL and its false flag unchanged."""
        self.assertEqual(
            DATA_ENDPOINTS["reading_request"],
            f"{API_URL}/api/connection/reading-request?withReadingNumberOnly=false",
        )

    async def test_get_reading_request_uses_authenticated_client(self) -> None:
        """Preserve the list payload and use the existing authenticated request."""
        api = self._build_api()
        response_data = [self._reading_request()]

        with patch.object(api, "_request", new_callable=AsyncMock) as request_mock:
            request_mock.return_value = response_data
            result = await api.get_reading_request()

        self.assertEqual(result, response_data)
        self.assertEqual(result[0]["readingNumber"], "00000000001")
        request_mock.assert_awaited_once_with(
            "GET", DATA_ENDPOINTS["reading_request"], expect_list=True
        )

    async def test_empty_and_closed_requests_are_supported(self) -> None:
        """Allow no pending requests and a closed entry without a reading number."""
        api = self._build_api()
        for response_data in ([], [{"openReading": False}]):
            with self.subTest(response_data=response_data):
                with patch.object(api, "_request", new_callable=AsyncMock) as mock:
                    mock.return_value = response_data
                    self.assertEqual(await api.get_reading_request(), response_data)

    def test_json_shape_validation(self) -> None:
        """Validate response container types for both old and new endpoints."""
        reading_request = self._reading_request()
        self.assertEqual(
            PWNAPI._parse_json_response([reading_request], expect_list=True),
            [reading_request],
        )
        self.assertEqual(
            PWNAPI._parse_json_response([], expect_list=True), []
        )
        self.assertEqual(
            PWNAPI._parse_json_response({"sample": 1}, expect_list=False),
            {"sample": 1},
        )
        for invalid_response, expect_list in (
            ({"unexpected": "object"}, True),
            (["not an object"], True),
            ([{"openReading": True}], False),
        ):
            with self.subTest(expect_list=expect_list, response=invalid_response):
                with self.assertRaises(PWNAPIError):
                    PWNAPI._parse_json_response(
                        invalid_response, expect_list=expect_list
                    )

    async def test_rejects_malformed_open_reading_requests(self) -> None:
        """Reject missing or invalid required metadata without logging it."""
        api = self._build_api()
        for invalid_entry in (
            {"openReading": "true"},
            {"openReading": True, "readingNumber": None, "amountOfWheels": 5},
            {"openReading": True, "readingNumber": "00000000001"},
            {
                "openReading": True,
                "readingNumber": "00000000001",
                "amountOfWheels": True,
            },
            {
                "openReading": True,
                "readingNumber": "00000000001",
                "amountOfWheels": 0,
            },
        ):
            with self.subTest(invalid_entry=invalid_entry):
                with patch.object(api, "_request", new_callable=AsyncMock) as mock:
                    mock.return_value = [invalid_entry]
                    with self.assertRaises(PWNAPIError):
                        await api.get_reading_request()
