"""Tests for the PWN meter-reading plausibility endpoint URL."""

import unittest

from .api import PWNAPI
from .const import API_URL, DATA_ENDPOINTS


class TestPlausibleReadingURL(unittest.TestCase):
    """Verify identifiers are safe and unchanged before future POST support."""

    def test_confirmed_endpoint_and_identifier_lengths(self) -> None:
        """Keep the 11-digit opname number and append three relation zeroes."""
        self.assertEqual(
            DATA_ENDPOINTS["water_usage_validate_plausible_reading"],
            f"{API_URL}/api/WaterUsage/validate-plausible-reading",
        )
        self.assertEqual(
            PWNAPI.build_plausible_reading_url("12345678901", "1234567"),
            (
                f"{API_URL}/api/WaterUsage/"
                "validate-plausible-reading/12345678901/0001234567"
            ),
        )

    def test_zero_in_opname_number_is_not_removed(self) -> None:
        """Preserve all 11 digits, even when the opname number starts with 0."""
        self.assertEqual(
            PWNAPI.build_plausible_reading_url("01234567890", "0012345"),
            (
                f"{API_URL}/api/WaterUsage/"
                "validate-plausible-reading/01234567890/0000012345"
            ),
        )

    def test_reject_invalid_opname_numbers(self) -> None:
        """Reject unsafe, short, long, and non-ASCII opname numbers."""
        invalid: tuple[object, ...] = (
            None,
            "",
            "1234567890",
            "123456789012",
            "1234567890/",
            "12345 78901",
            "１２３４５６７８９０１",
            12345678901,
        )
        for opname_number in invalid:
            with self.subTest(value_type=type(opname_number).__name__):
                with self.assertRaisesRegex(ValueError, "Invalid reading number"):
                    PWNAPI.build_plausible_reading_url(
                        opname_number, "1234567"
                    )

    def test_reject_invalid_relation_numbers(self) -> None:
        """Reject already-prefixed IDs rather than silently double-prefixing."""
        invalid: tuple[object, ...] = (
            None,
            "",
            "123456",
            "12345678",
            "0001234567",
            "123/567",
            "１２３４５６７",
            1234567,
        )
        for relation_number in invalid:
            with self.subTest(value_type=type(relation_number).__name__):
                with self.assertRaisesRegex(ValueError, "Invalid relation number"):
                    PWNAPI.build_plausible_reading_url(
                        "12345678901", relation_number
                    )
