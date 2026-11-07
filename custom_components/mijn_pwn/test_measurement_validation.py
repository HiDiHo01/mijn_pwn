"""Regression tests for PWN meter-reading request validation."""

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.helpers.update_coordinator import UpdateFailed

from .api import PWNAPI, PWNAPIError
from .const import API_URL, DATA_ENDPOINTS


class TestMeasurementValidation(unittest.IsolatedAsyncioTestCase):
    """Exercise the validation endpoint without touching a real account."""

    @staticmethod
    def _make_client() -> PWNAPI:
        """Build a client with fictitious credentials and no network transport."""
        return PWNAPI(
            username="synthetic@example.invalid",
            password="synthetic-password",
            session=MagicMock(),
        )

    @staticmethod
    def _address() -> dict[str, object]:
        """Return the confirmed address request shape using fictitious values."""
        return {
            "houseNumber": "001",
            "houseNumberAddition": "",
            "postalCode": "0000 AA",
        }

    @staticmethod
    def _validation_response() -> dict[str, object]:
        """Construct a fictional token-bearing validation response."""
        return {
            "relationNumber": "0123456789",
            "clientType": 1,
            "additionalDataToken": "synthetic-not-a-live-token",
            "readingReason": 1,
            "periodStartDate": None,
            "periodEndDate": "2026-11-21T00:00:00.0000000Z",
            "houseNumberAddition": "",
        }

    async def test_posts_only_confirmed_address_fields(self) -> None:
        """Keep the reading identifier intact and exclude unrelated fields."""
        client = self._make_client()
        data = self._validation_response()
        supplied_address = {
            **self._address(),
            "street": "Do not submit this value",
            "private": "ignored",
        }
        with patch.object(client, "_request", new_callable=AsyncMock) as mock_request:
            mock_request.return_value = {**data, "other": "ignored"}

            result = await client.validate_measurement_request(
                "99999999991", supplied_address
            )

        self.assertEqual(result, data)
        mock_request.assert_awaited_once_with(
            "POST",
            f"{API_URL}/api/WaterUsage/validate-measurement-request/99999999991",
            json=self._address(),
        )
        self.assertEqual(
            DATA_ENDPOINTS["water_usage_validate_measurement_request"],
            f"{API_URL}/api/WaterUsage/validate-measurement-request",
        )

    async def test_zero_start_reading_number_is_sent_unchanged(self) -> None:
        """Use every digit of an existing 11-digit reading identifier as given."""
        client = self._make_client()
        response = self._validation_response()
        with patch.object(client, "_request", new_callable=AsyncMock) as request_mock:
            request_mock.return_value = response

            result = await client.validate_measurement_request(
                "01234567890", self._address()
            )

        self.assertEqual(result, response)
        request_mock.assert_awaited_once_with(
            "POST",
            f"{API_URL}/api/WaterUsage/validate-measurement-request/01234567890",
            json=self._address(),
        )

    async def test_rejects_invalid_reading_number_without_post(self) -> None:
        """Reject reading numbers that are not exactly 11 ASCII digits."""
        client = self._make_client()
        cases: tuple[object, ...] = (
            "",
            "99999999991/",
            "read-123",
            "１２３４５６７８９０１",
            "1" * 10,
            "1" * 12,
            "1" * 33,
            "123 456",
            12345678901,
        )
        with patch.object(client, "_request", new_callable=AsyncMock) as mock_request:
            for reading_number in cases:
                with self.subTest(input_type=type(reading_number).__name__):
                    with self.assertRaisesRegex(ValueError, "Invalid reading number"):
                        await client.validate_measurement_request(
                            reading_number, self._address()
                        )

        mock_request.assert_not_awaited()

    async def test_rejects_invalid_address_without_post(self) -> None:
        """Validate every request field before making a POST request."""
        client = self._make_client()
        address = self._address()
        cases: tuple[object, ...] = (
            None,
            {},
            [],
            {**address, "postalCode": ""},
            {**address, "houseNumber": 1},
            {**address, "houseNumberAddition": None},
            {**address, "houseNumber": "X" * 256},
        )
        with patch.object(client, "_request", new_callable=AsyncMock) as mock_request:
            for invalid_address in cases:
                with self.subTest(address_type=type(invalid_address).__name__):
                    with self.assertRaisesRegex(
                        ValueError, "Invalid measurement validation address"
                    ):
                        await client.validate_measurement_request(
                            "99999999991", invalid_address
                        )

        mock_request.assert_not_awaited()

    def test_rejects_malformed_validation_responses(self) -> None:
        """Ensure missing token, wrong types, and malformed dates are rejected."""
        valid = self._validation_response()
        cases: tuple[object, ...] = (
            None,
            [],
            {"response": valid},
            {**valid, "additionalDataToken": ""},
            {**valid, "additionalDataToken": None},
            {**valid, "relationNumber": "ABC123"},
            {**valid, "relationNumber": ""},
            {**valid, "clientType": True},
            {**valid, "readingReason": -1},
            {**valid, "periodStartDate": False},
            {**valid, "periodEndDate": ""},
            {**valid, "houseNumberAddition": None},
        )
        for data in cases:
            with self.subTest(response_type=type(data).__name__):
                with self.assertRaisesRegex(
                    PWNAPIError, "Invalid measurement validation response"
                ) as error:
                    PWNAPI.parse_measurement_validation(data)
                self.assertNotIn("synthetic-not-a-live-token", str(error.exception))

    async def test_upstream_error_is_redacted(self) -> None:
        """Keep potentially sensitive error response text out of failures."""
        client = self._make_client()
        with patch.object(
            client,
            "_request",
            new_callable=AsyncMock,
            side_effect=UpdateFailed("private-name synthetic-not-a-live-token"),
        ):
            with self.assertRaisesRegex(
                UpdateFailed, "Unable to validate meter-reading request"
            ) as error:
                await client.validate_measurement_request(
                    "99999999991", self._address()
                )
        self.assertNotIn("synthetic-not-a-live-token", str(error.exception))
