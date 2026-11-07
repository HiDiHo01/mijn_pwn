"""No-network regression tests for final PWN water meter reading submission."""

from datetime import date
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed

from .api import PWNAPI, PWNAPIError, PWNReadingSubmissionRejected
from .const import API_URL, DATA_ENDPOINTS


class TestFinalReadingSubmission(unittest.IsolatedAsyncioTestCase):
    """Verify exact endpoint behavior and fail-closed server confirmation."""

    @staticmethod
    def _api() -> PWNAPI:
        """Create a PWN client using only fictional credentials."""
        return PWNAPI(
            username="synthetic@example.invalid",
            password="synthetic-password",
            session=MagicMock(),
        )

    @staticmethod
    def _address() -> dict[str, object]:
        """Provide a fully fictional connection address."""
        return {
            "postalCode": "0000 AA",
            "street": "Synthetic Lane",
            "houseNumber": "1",
            "houseNumberAddition": "",
        }

    async def test_success_posts_exact_case_sensitive_json(self) -> None:
        """Accept only the final POST response with a matching JSON acknowledgement."""
        api = self._api()
        response = {"success": True, "errorText": ""}
        self.assertEqual(
            DATA_ENDPOINTS["water_usage_submit_reading"],
            f"{API_URL}/api/WaterUsage",
        )
        address = {**self._address(), "discard": "private-data"}
        with patch.object(api, "_request", new_callable=AsyncMock) as request_mock:
            request_mock.return_value = {**response, "ignore": "private"}
            result = await api.submit_meter_reading(
                "01234567890", 165, date(2026, 11, 7), address, 5
            )
        self.assertEqual(result, response)
        request_mock.assert_awaited_once_with(
            "POST",
            f"{API_URL}/api/WaterUsage/01234567890",
            json={
                "MeterReading": 165,
                "AnomalyReason": None,
                "ReadingDate": "2026-11-07",
                "PostalCode": "0000 AA",
                "Street": "Synthetic Lane",
                "HouseNumber": "1",
                "HouseNumberAddition": "",
                "WaterMeterImage": None,
            },
            expected_status=200,
        )

    def test_reject_failed_and_malformed_responses(self) -> None:
        """Never infer success from HTTP alone or reveal upstream error text."""
        for response in (
            {"success": False, "errorText": ""},
            {"success": False, "errorText": "fictional-personal-information"},
        ):
            with self.subTest(response=response):
                with self.assertRaises(PWNReadingSubmissionRejected) as error:
                    PWNAPI.parse_final_reading_response(response)
                self.assertNotIn("fictional-personal-information", str(error.exception))

        for response in (
            None,
            [],
            {"success": True},
            {"success": True, "errorText": None},
            {"success": True, "errorText": "unexpected"},
            {"success": "true", "errorText": ""},
            {"success": 1, "errorText": ""},
            {"success": None, "errorText": ""},
        ):
            with self.subTest(response=response):
                with self.assertRaises(PWNAPIError):
                    PWNAPI.parse_final_reading_response(response)

    def test_explicit_http_status_guard(self) -> None:
        """Only verified HTTP 200 may acknowledge the final JSON response."""
        PWNAPI._check_expected_status(200, 200)
        PWNAPI._check_expected_status(204, None)
        for code in (201, 202, 204, 400, 500):
            with self.subTest(code=code):
                with self.assertRaises(UpdateFailed):
                    PWNAPI._check_expected_status(code, 200)

    async def test_errors_are_redacted(self) -> None:
        """Avoid surfacing private upstream error bodies to Home Assistant."""
        api = self._api()
        reading_date = date(2026, 11, 7)
        address = self._address()
        with patch.object(
            api,
            "_request",
            new_callable=AsyncMock,
            side_effect=UpdateFailed("fictional-account-data"),
        ):
            with self.assertRaisesRegex(
                UpdateFailed, "Unable to submit meter reading to PWN"
            ) as error:
                await api.submit_meter_reading(
                    "01234567890", 165, reading_date, address, 5
                )
        self.assertNotIn("fictional-account-data", str(error.exception))

    async def test_auth_failure_does_not_replay_final_post(self) -> None:
        """Never replay an irreversible POST when its response is HTTP 401."""
        api = self._api()
        api._access_token = "synthetic-token"
        response = MagicMock(status=401)
        api._session.request.return_value.__aenter__.return_value = response
        reading_date = date(2026, 11, 7)
        address = self._address()

        with self.assertRaises(ConfigEntryAuthFailed):
            await api.submit_meter_reading(
                "01234567890", 165, reading_date, address, 5
            )

        api._session.request.assert_called_once()
        self.assertEqual(api._session.request.call_args.args[0], "POST")

    async def test_invalid_reading_and_address_never_post(self) -> None:
        """Check the full request before any final submission API call."""
        api = self._api()
        reading_date = date(2026, 11, 7)
        with patch.object(api, "_request", new_callable=AsyncMock) as request_mock:
            for reading_number, reading, address in (
                ("1234567890", 165, self._address()),
                ("x" * 11, 165, self._address()),
                ("01234567890", 100000, self._address()),
                ("01234567890", 165, {**self._address(), "street": ""}),
                ("01234567890", 165, {**self._address(), "street": 123}),
            ):
                with self.subTest(reading_number=reading_number):
                    with self.assertRaises(ValueError):
                        await api.submit_meter_reading(
                            reading_number, reading, reading_date, address, 5
                        )
            request_mock.assert_not_awaited()
