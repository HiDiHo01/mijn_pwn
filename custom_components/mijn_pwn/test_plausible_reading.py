"""Tests for the PWN meter-reading plausibility endpoint URL."""

from datetime import date, datetime, timezone
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

    @staticmethod
    def _address() -> dict[str, object]:
        """Create fictional address fields for request-body tests."""
        return {
            "postalCode": "0000 AA",
            "houseNumber": "1",
            "houseNumberAddition": "",
        }

    def test_confirmed_json_payload_without_padding(self) -> None:
        """Send an integer reading, ISO date, and only the three address fields."""
        address = {
            **self._address(),
            "street": "Unused street",
            "private": "must-not-be-sent",
        }
        result = PWNAPI._build_plausible_reading_payload(
            165, date(2026, 11, 7), address, 5
        )
        self.assertEqual(
            result,
            {
                "meterReading": 165,
                "readingDate": "2026-11-07",
                "postalCode": "0000 AA",
                "houseNumber": "1",
                "houseNumberAddition": "",
            },
        )
        self.assertEqual(result["meterReading"], 165)
        self.assertNotIn("street", result)
        self.assertNotIn("private", result)

    def test_meter_reading_bounds_and_boolean_rejection(self) -> None:
        """Reject values outside the wheel range without converting inputs."""
        for meter_reading, wheel_count in (
            (-1, 5),
            (100000, 5),
            (1.5, 5),
            ("165", 5),
            (True, 5),
            (165, 0),
            (165, 10),
            (165, True),
        ):
            with self.subTest(
                meter_type=type(meter_reading).__name__, wheel_count=wheel_count
            ):
                with self.assertRaisesRegex(ValueError, "Invalid meter"):
                    PWNAPI._build_plausible_reading_payload(
                        meter_reading, date(2026, 11, 7), self._address(), wheel_count
                    )
        self.assertEqual(
            PWNAPI._build_plausible_reading_payload(
                0, date(2026, 11, 7), self._address(), 5
            )["meterReading"],
            0,
        )
        self.assertEqual(
            PWNAPI._build_plausible_reading_payload(
                99999, date(2026, 11, 7), self._address(), 5
            )["meterReading"],
            99999,
        )

    def test_reading_date_must_be_a_date_not_timestamp_or_text(self) -> None:
        """Prevent accidental timezone conversion or unvalidated date strings."""
        invalid_dates: tuple[object, ...] = (
            None,
            "2026-11-07",
            "07-11-2026",
            datetime(2026, 11, 7, tzinfo=timezone.utc),
        )
        for reading_date in invalid_dates:
            with self.subTest(date_type=type(reading_date).__name__):
                with self.assertRaisesRegex(ValueError, "Invalid reading date"):
                    PWNAPI._build_plausible_reading_payload(
                        165, reading_date, self._address(), 5
                    )

    def test_plausibility_payload_address_validation(self) -> None:
        """Reject missing or malformed fields before they reach an HTTP request."""
        address = self._address()
        invalid_addresses: tuple[object, ...] = (
            None,
            [],
            {},
            {**address, "postalCode": ""},
            {**address, "houseNumber": 1},
            {**address, "houseNumberAddition": None},
            {**address, "postalCode": "X" * 256},
        )
        for invalid_address in invalid_addresses:
            with self.subTest(address_type=type(invalid_address).__name__):
                with self.assertRaisesRegex(ValueError, "Invalid plausibility address"):
                    PWNAPI._build_plausible_reading_payload(
                        165, date(2026, 11, 7), invalid_address, 5
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
                    PWNAPI.build_plausible_reading_url(opname_number, "1234567")

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
                    PWNAPI.build_plausible_reading_url("12345678901", relation_number)
