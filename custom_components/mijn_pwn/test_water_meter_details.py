"""Regression tests for the Mijn PWN WaterUsage meter-details response."""

import unittest

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
                with self.assertRaisesRegex(
                    PWNAPIError, "Invalid water meter details"
                ):
                    PWNAPI.parse_water_meter_details(payload)
