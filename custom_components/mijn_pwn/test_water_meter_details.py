"""Regression tests for the Mijn PWN WaterUsage meter-details response."""

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.helpers.update_coordinator import UpdateFailed

from .api import PWNAPI, PWNAPIError
from .const import API_ENDPOINTS, API_URL, DATA_ENDPOINTS


class TestWaterMeterDetails(unittest.TestCase):
    """Validate meter-wheel metadata without making POST requests."""

    def test_endpoint_registration(self) -> None:
        """Preserve the confirmed WaterUsage endpoint and its URL casing."""
        expected = f"{API_URL}/api/WaterUsage/meter-details"
        self.assertEqual(API_ENDPOINTS["water_usage_meter_details"], expected)
        self.assertEqual(DATA_ENDPOINTS["water_usage_meter_details"], expected)

    def test_empty_meter_type_and_no_red_wheels(self) -> None:
        """Accept the confirmed response with five black and zero red wheels."""
        payload: dict[str, object] = {
            "meterType": "",
            "amountOfBlackWheels": 5,
            "amountOfRedWheels": 0,
        }

        self.assertEqual(PWNAPI.parse_water_meter_details(payload), payload)

    def test_valid_numeric_wheel_counts(self) -> None:
        """Preserve explicit wheel counts without guessing meter semantics."""
        payload = {
            "meterType": "synthetic-type",
            "amountOfBlackWheels": 4,
            "amountOfRedWheels": 2,
            "unrelated": "do-not-expose",
        }

        result = PWNAPI.parse_water_meter_details(payload)

        self.assertEqual(result["amountOfBlackWheels"], 4)
        self.assertEqual(result["amountOfRedWheels"], 2)
        self.assertNotIn("unrelated", result)

    def test_malformed_responses(self) -> None:
        """Reject invalid JSON shape or wheel counts, including booleans."""
        valid_payload: dict[str, object] = {
            "meterType": "",
            "amountOfBlackWheels": 5,
            "amountOfRedWheels": 0,
        }
        cases: tuple[object, ...] = (
            None,
            [],
            {"reading": valid_payload},
            {**valid_payload, "meterType": None},
            {**valid_payload, "amountOfBlackWheels": True},
            {**valid_payload, "amountOfBlackWheels": -1},
            {**valid_payload, "amountOfBlackWheels": "5"},
            {**valid_payload, "amountOfRedWheels": -1},
            {**valid_payload, "amountOfRedWheels": False},
            {key: value for key, value in valid_payload.items() if key != "meterType"},
        )
        for payload in cases:
            with self.subTest(payload_type=type(payload).__name__):
                with self.assertRaisesRegex(PWNAPIError, "Invalid water meter details"):
                    PWNAPI.parse_water_meter_details(payload)


class TestWaterMeterDetailsRequest(unittest.IsolatedAsyncioTestCase):
    """Check POST payload construction with no real PWN network traffic."""

    @staticmethod
    def _make_client() -> PWNAPI:
        """Return an isolated API client using synthetic account credentials."""
        return PWNAPI(
            username="test@example.invalid",
            password="synthetic-password",
            session=MagicMock(),
        )

    @staticmethod
    def _address() -> dict[str, object]:
        """Return a fictional address matching the confirmed request payload."""
        return {
            "postalCode": "0000 AA",
            "street": "Voorbeeldstraat",
            "houseNumber": "001",
            "houseNumberAddition": "",
        }

    async def test_post_payload_and_response(self) -> None:
        """Send only the confirmed four fields and parse the response."""
        client = self._make_client()
        supplied_address = {**self._address(), "country": "NL", "private": "omit"}
        response = {
            "meterType": "",
            "amountOfBlackWheels": 5,
            "amountOfRedWheels": 0,
        }
        with patch.object(client, "_request", new_callable=AsyncMock) as mock_request:
            mock_request.return_value = response

            result = await client.get_water_meter_details(supplied_address)

        self.assertEqual(result, response)
        mock_request.assert_awaited_once_with(
            "POST",
            DATA_ENDPOINTS["water_usage_meter_details"],
            json=self._address(),
        )

    async def test_reject_invalid_address_before_post(self) -> None:
        """Never send a request with missing or incorrectly typed address fields."""
        client = self._make_client()
        address = self._address()
        invalid_addresses: tuple[object, ...] = (
            None,
            [],
            {},
            {**address, "postalCode": ""},
            {**address, "street": None},
            {**address, "houseNumber": 1},
            {**address, "houseNumberAddition": None},
            {**address, "street": "X" * 256},
        )
        with patch.object(client, "_request", new_callable=AsyncMock) as mock_request:
            for invalid_address in invalid_addresses:
                with self.subTest(address_type=type(invalid_address).__name__):
                    with self.assertRaisesRegex(
                        ValueError, "Invalid water meter address"
                    ):
                        await client.get_water_meter_details(invalid_address)

        mock_request.assert_not_awaited()

    async def test_untrusted_api_error_is_redacted(self) -> None:
        """Do not expose upstream response bodies that may include addresses."""
        client = self._make_client()
        with patch.object(
            client,
            "_request",
            new_callable=AsyncMock,
            side_effect=UpdateFailed("Bad request: private street 123"),
        ):
            with self.assertRaisesRegex(
                UpdateFailed, "Unable to retrieve water meter details"
            ) as captured:
                await client.get_water_meter_details(self._address())
        self.assertNotIn("private street", str(captured.exception))
