"""Tests for the PWN meter-reading plausibility endpoint URL."""

from datetime import date, datetime, timezone
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.helpers.update_coordinator import UpdateFailed

from .api import PWNAPI, PWNAPIError
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


class TestPlausibilityResponse(unittest.TestCase):
    """Validate PWN plausibility responses without changing submission state."""

    def test_true_and_null_bounds(self) -> None:
        """Accept the exact observed response while excluding unknown fields."""
        response = {
            "isPlausible": True,
            "minReading": None,
            "maxReading": None,
            "private": "never-return-this-field",
        }
        self.assertEqual(
            PWNAPI.parse_plausible_reading_response(response),
            {"isPlausible": True, "minReading": None, "maxReading": None},
        )

    def test_false_with_nullable_or_numeric_bounds(self) -> None:
        """Return nonplausible responses instead of claiming a submission."""
        for response in (
            {"isPlausible": False, "minReading": None, "maxReading": None},
            {"isPlausible": False, "minReading": 100, "maxReading": 200},
            {"isPlausible": False, "minReading": 100.5, "maxReading": None},
            {"isPlausible": True, "minReading": None, "maxReading": 200},
        ):
            with self.subTest(response=response):
                self.assertEqual(
                    PWNAPI.parse_plausible_reading_response(response),
                    response,
                )

    def test_invalid_shapes_flags_and_bounds(self) -> None:
        """Reject booleans as numeric bounds and malformed values as decisions."""
        valid = {"isPlausible": True, "minReading": None, "maxReading": None}
        cases: tuple[object, ...] = (
            None,
            [],
            {},
            {"result": valid},
            {**valid, "isPlausible": "true"},
            {**valid, "isPlausible": 1},
            {**valid, "isPlausible": None},
            {"isPlausible": True, "minReading": None},
            {"isPlausible": True, "maxReading": None},
            {**valid, "minReading": True},
            {**valid, "maxReading": False},
            {**valid, "minReading": -1},
            {**valid, "maxReading": "250"},
            {**valid, "minReading": float("nan")},
            {**valid, "maxReading": float("inf")},
            {**valid, "minReading": 200, "maxReading": 100},
        )
        for response in cases:
            with self.subTest(response_type=type(response).__name__):
                with self.assertRaisesRegex(
                    PWNAPIError, "Invalid plausibility response"
                ):
                    PWNAPI.parse_plausible_reading_response(response)


class TestPlausibilityAPI(unittest.IsolatedAsyncioTestCase):
    """Verify the optional POST does not confirm or persist a meter reading."""

    @staticmethod
    def _make_client() -> PWNAPI:
        """Create a client with fictional credentials and mocked HTTP transport."""
        return PWNAPI(
            username="synthetic@example.invalid",
            password="synthetic-password",
            session=MagicMock(),
        )

    @staticmethod
    def _address() -> dict[str, object]:
        """Provide fictional, minimal address data."""
        return {"postalCode": "0000 AA", "houseNumber": "1", "houseNumberAddition": ""}

    async def test_post_result_never_marks_reading_submitted(self) -> None:
        """Leave confirmed-submission sensors untouched after isPlausible=true."""
        from .coordinator import PWNDataUpdateCoordinator

        api = self._make_client()
        response = {"isPlausible": True, "minReading": None, "maxReading": None}
        with (
            patch.object(api, "_request", new_callable=AsyncMock) as mock_request,
            patch.object(
                PWNDataUpdateCoordinator,
                "record_confirmed_meter_submission",
            ) as mock_record,
        ):
            mock_request.return_value = response
            result = await api.validate_plausible_reading(
                "01234567890",
                "0012345",
                165,
                date(2026, 11, 7),
                self._address(),
                5,
            )

        self.assertEqual(result, response)
        mock_request.assert_awaited_once_with(
            "POST",
            f"{API_URL}/api/WaterUsage/validate-plausible-reading/01234567890/0000012345",
            json={
                "meterReading": 165,
                "readingDate": "2026-11-07",
                **self._address(),
            },
        )
        mock_record.assert_not_called()

    async def test_upstream_error_redacts_private_data(self) -> None:
        """Strip PWN error responses that might contain account identifiers."""
        api = self._make_client()
        with patch.object(
            api,
            "_request",
            new_callable=AsyncMock,
            side_effect=UpdateFailed("private address and reading from upstream"),
        ):
            with self.assertRaisesRegex(
                UpdateFailed, "Unable to validate meter-reading plausibility"
            ) as error:
                await api.validate_plausible_reading(
                    "01234567890", "0012345", 165, date(2026, 11, 7), self._address(), 5
                )
        self.assertNotIn("private address", str(error.exception))

    async def test_invalid_inputs_never_trigger_post(self) -> None:
        """Validate URL identifiers and JSON fields before network requests."""
        api = self._make_client()
        with patch.object(api, "_request", new_callable=AsyncMock) as mock_request:
            with self.assertRaises(ValueError):
                await api.validate_plausible_reading(
                    "bad-id", "0012345", 165, date(2026, 11, 7), self._address(), 5
                )
            with self.assertRaises(ValueError):
                await api.validate_plausible_reading(
                    "01234567890",
                    "0012345",
                    100000,
                    date(2026, 11, 7),
                    self._address(),
                    5,
                )
            mock_request.assert_not_awaited()
