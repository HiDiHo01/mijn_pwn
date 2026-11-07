"""Regression tests for Mijn PWN private information confirmation URL."""

import unittest

from .api import PWNAPI
from .const import API_URL, DATA_ENDPOINTS


class TestPrivateConfirmationUrl(unittest.TestCase):
    """Verify URL construction without submitting any customer details."""

    def test_identifier_has_exactly_ten_digits(self) -> None:
        """Build a 10-digit identifier with exactly three leading zeroes."""
        url = PWNAPI.build_private_confirmation_url("1234567")

        self.assertEqual(url, f"{API_URL}/api/SupplementInformation/private/0001234567")
        self.assertEqual(
            DATA_ENDPOINTS["supplement_information_private"],
            f"{API_URL}/api/SupplementInformation/private",
        )

    def test_existing_leading_zeroes_are_preserved(self) -> None:
        """Do not coerce seven-digit relation numbers to integers."""
        self.assertEqual(
            PWNAPI.build_private_confirmation_url("0012345"),
            f"{API_URL}/api/SupplementInformation/private/0000012345",
        )

    def test_invalid_relation_numbers_are_rejected(self) -> None:
        """Reject unsafe, non-ASCII, short, and long identifiers."""
        invalid_numbers: tuple[object, ...] = (
            "",
            "123456",
            "12345678",
            "123 567",
            "123/567",
            "-123456",
            "１２３４５６７",
            "\n123456",
            1234567,
        )
        for invalid_number in invalid_numbers:
            with self.subTest(invalid_type=type(invalid_number).__name__):
                with self.assertRaisesRegex(ValueError, "exactly seven ASCII digits"):
                    PWNAPI.build_private_confirmation_url(invalid_number)
