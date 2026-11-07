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
            "readingNumber": "99999999991",
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
        self.assertEqual(result[0]["readingNumber"], "99999999991")
        request_mock.assert_awaited_once_with(
            "GET", DATA_ENDPOINTS["reading_request"], expect_list=True
        )

    async def test_zero_starting_reading_number_is_preserved(self) -> None:
        """Accept an existing initial zero without adding or removing digits."""
        api = self._build_api()
        sample = self._reading_request()
        sample["readingNumber"] = "01234567890"

        with patch.object(api, "_request", new_callable=AsyncMock) as request_mock:
            request_mock.return_value = [sample]
            result = await api.get_reading_request()

        self.assertEqual(result[0]["readingNumber"], "01234567890")

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
        self.assertEqual(PWNAPI._parse_json_response([], expect_list=True), [])
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

    async def test_isolates_malformed_requests(self) -> None:
        """Preserve valid meters when another request has invalid metadata."""
        api = self._build_api()
        for invalid_entry in (
            {"openReading": "true"},
            {"openReading": True, "readingNumber": None, "amountOfWheels": 5},
            {"openReading": True, "readingNumber": "1" * 10, "amountOfWheels": 5},
            {"openReading": True, "readingNumber": "1" * 12, "amountOfWheels": 5},
            {"openReading": True, "readingNumber": "abcdefghijk", "amountOfWheels": 5},
            {"openReading": True, "readingNumber": "99999999991"},
            {
                "openReading": True,
                "readingNumber": "99999999991",
                "amountOfWheels": True,
            },
            {
                "openReading": True,
                "readingNumber": "99999999991",
                "amountOfWheels": 0,
            },
        ):
            with self.subTest(invalid_entry=invalid_entry):
                with patch.object(api, "_request", new_callable=AsyncMock) as mock:
                    mock.return_value = [invalid_entry]
                    valid = self._reading_request()
                    result = await api.get_reading_request()
                    self.assertEqual(result, [invalid_entry])
                    mock.return_value = [invalid_entry, valid]
                    self.assertEqual(
                        await api.get_reading_request(),
                        [invalid_entry, valid],
                    )

    async def test_redacts_upstream_meter_request_error(self) -> None:
        """Remove private upstream response content from public error messages."""
        from homeassistant.helpers.update_coordinator import UpdateFailed

        api = self._build_api()
        with patch.object(
            api,
            "_request",
            new_callable=AsyncMock,
            side_effect=UpdateFailed("private-request-content"),
        ):
            with self.assertRaisesRegex(
                UpdateFailed, "Unable to retrieve meter-reading requests"
            ) as error:
                await api.get_reading_request()
        self.assertNotIn("private-request-content", str(error.exception))

    async def test_notification_arrays_remain_available_to_count_parser(self) -> None:
        """Allow empty arrays and objects; reject unsupported JSON shapes."""
        api = self._build_api()
        with patch.object(api, "_request", new_callable=AsyncMock) as request:
            for response in ([], [{"id": "synthetic"}], {"unverified": True}):
                request.return_value = response
                self.assertEqual(await api.get_notifications(), response)
            request.assert_awaited_with(
                "GET", DATA_ENDPOINTS["notifications"], expect_list=None
            )

        self.assertEqual(
            PWNAPI._parse_json_response([], expect_list=None), []
        )
        self.assertEqual(
            PWNAPI._parse_json_response([{}], expect_list=None), [{}]
        )
        self.assertEqual(
            PWNAPI._parse_json_response(["synthetic notice"], expect_list=None),
            ["synthetic notice"],
        )
        for response in (None, "invalid", 1):
            with self.subTest(response=response):
                with self.assertRaises(PWNAPIError):
                    PWNAPI._parse_json_response(response, expect_list=None)
