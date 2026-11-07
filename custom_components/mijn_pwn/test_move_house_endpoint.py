"""Regression tests for the future Mijn PWN moving-address endpoint."""

import unittest

from .api import PWNAPI, PWNAPIError
from .const import API_ENDPOINTS, API_URL, DATA_ENDPOINTS


class TestMoveHouseEndpoint(unittest.TestCase):
    """Keep the unverified MoveHouse endpoint registered without invoking it."""

    def test_validate_address_endpoint(self) -> None:
        """Preserve the original URL casing and the shared endpoint mapping."""
        expected_url = f"{API_URL}/api/MoveHouse/validate-address"
        self.assertEqual(API_ENDPOINTS["move_house_validate_address"], expected_url)
        self.assertEqual(DATA_ENDPOINTS["move_house_validate_address"], expected_url)

    def test_parse_validated_address(self) -> None:
        """Preserve the six API fields, including an empty address addition."""
        payload: dict[str, object] = {
            "street": "Voorbeeldstraat",
            "houseNumber": "001",
            "houseNumberAddition": "",
            "postalCode": "0000 AA",
            "city": "Voorbeeldstad",
            "country": "NL",
        }

        result = PWNAPI.parse_move_house_address(payload)

        self.assertEqual(result, payload)
        self.assertIsInstance(result["houseNumber"], str)
        self.assertEqual(result["houseNumberAddition"], "")

    def test_rejects_invalid_address_responses(self) -> None:
        """Reject absent or badly typed fields without including address data."""
        valid_payload = {
            "street": "Voorbeeldstraat",
            "houseNumber": "001",
            "houseNumberAddition": "",
            "postalCode": "0000 AA",
            "city": "Voorbeeldstad",
            "country": "NL",
        }
        invalid_payloads: list[object] = [
            None,
            [],
            {"address": valid_payload},
            {**valid_payload, "city": None},
            {**valid_payload, "postalCode": ""},
            {**valid_payload, "houseNumber": 1},
            {key: value for key, value in valid_payload.items() if key != "country"},
        ]
        for payload in invalid_payloads:
            with self.subTest(payload_type=type(payload).__name__):
                with self.assertRaisesRegex(
                    PWNAPIError, "Invalid MoveHouse address response"
                ) as raised:
                    PWNAPI.parse_move_house_address(payload)
                self.assertNotIn("Voorbeeldstraat", str(raised.exception))

    def test_ignores_unrecognized_fields(self) -> None:
        """Never persist unrelated fields returned by the upstream endpoint."""
        payload = {
            "street": "Voorbeeldstraat",
            "houseNumber": "1",
            "houseNumberAddition": "",
            "postalCode": "0000 AA",
            "city": "Voorbeeldstad",
            "country": "NL",
            "unrecognized": "private-example-data",
        }
        parsed = PWNAPI.parse_move_house_address(payload)
        self.assertNotIn("unrecognized", parsed)
