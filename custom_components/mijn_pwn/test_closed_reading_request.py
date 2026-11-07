"""Offline regression tests for closed PWN meter-reading requests (sap-01)."""

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import UpdateFailed

from .api import PWNAPI, PWNReadingRequestClosedError
from .button import PWNPrepareMeterReadingButton, PWNSubmitMeterReadingButton
from .const import DATA_READING_REQUESTS
from .coordinator import PWNDataUpdateCoordinator
from .date import PWNReadingDate
from .number import PWNReadingDraftNumber
from .reading_request import (
    READING_REQUEST_SENSOR_DESCRIPTIONS,
    PWNReadingRequestBinarySensor,
    PWNReadingRequestSensor,
)
from .test_submit_workflow import _coordinator


def _entry() -> MagicMock:
    """Build a fictitious Home Assistant config entry."""
    entry = MagicMock()
    entry.entry_id = "fictional-entry"
    entry.unique_id = "fictional-account"
    return entry


class TestClosedRequestAPI(unittest.IsolatedAsyncioTestCase):
    """Classify only HTTP 400 sap-01 from measurement-request validation."""

    @staticmethod
    def _api() -> PWNAPI:
        """Create an offline API client with synthetic credentials."""
        return PWNAPI("synthetic@example.invalid", "synthetic-password", MagicMock())

    @staticmethod
    def _response(code: object, *, status: int = 400) -> MagicMock:
        """Provide a mocked HTTP response with fictional upstream HTML."""
        response = MagicMock()
        response.status = status
        response.json = AsyncMock(
            return_value={
                "code": code,
                "message": "<p>fictional-private-account-information</p>",
                "details": {},
            }
        )
        return response

    async def test_sap_01_is_a_closed_request_without_exposing_html(self) -> None:
        """Recognize the exact validation endpoint's specific 400 response."""
        response = self._response("sap-01")
        client = self._api()
        with self.assertRaises(PWNReadingRequestClosedError) as failure:
            await client._raise_for_status(
                response, is_measurement_validation=True
            )
        self.assertNotIn("fictional-private", str(failure.exception))
        response.json.assert_awaited_once_with(content_type=None)

    async def test_other_400_codes_are_not_terminal_rejections(self) -> None:
        """Keep unrelated malformed and ordinary 400 errors transient."""
        for code in ("sap-02", None, "", "SAP-01"):
            with self.subTest(code=code):
                response = self._response(code)
                client = self._api()
                with self.assertRaises(UpdateFailed) as failure:
                    await client._raise_for_status(
                        response, is_measurement_validation=True
                    )
                self.assertNotIn("fictional-private", str(failure.exception))

    async def test_sap_01_is_ignored_outside_measurement_validation(self) -> None:
        """Avoid interpreting the same code from unrelated API endpoints."""
        response = self._response("sap-01")
        client = self._api()
        with self.assertRaises(UpdateFailed):
            await client._raise_for_status(response)
        response.json.assert_not_awaited()

    async def test_http_500_does_not_close_request(self) -> None:
        """Server failures must not disable meter submission entities."""
        response = self._response("sap-01", status=500)
        client = self._api()
        with self.assertRaises(UpdateFailed):
            await client._raise_for_status(
                response, is_measurement_validation=True
            )
        response.json.assert_not_awaited()

    async def test_real_request_path_detects_sap_01_without_retry(self) -> None:
        """Propagate a specific HTTP 400 without retrying or exposing its body."""
        client = self._api()
        client._access_token = "fictional-access-token"
        response = self._response("sap-01")
        context = MagicMock()
        context.__aenter__ = AsyncMock(return_value=response)
        context.__aexit__ = AsyncMock(return_value=False)
        client._session.request.return_value = context

        with self.assertRaises(PWNReadingRequestClosedError):
            await client.validate_measurement_request(
                "01234567890",
                {
                    "postalCode": "0000 AA",
                    "houseNumber": "1",
                    "houseNumberAddition": "",
                },
            )

        client._session.request.assert_called_once()
        self.assertIn(
            "/validate-measurement-request/01234567890",
            client._session.request.call_args.args[1],
        )

    async def test_public_validation_method_preserves_closed_exception(self) -> None:
        """Do not convert a known terminal rejection into a generic failure."""
        client = self._api()
        with patch.object(
            client,
            "_request",
            new_callable=AsyncMock,
            side_effect=PWNReadingRequestClosedError("PWN request closed"),
        ):
            with self.assertRaises(PWNReadingRequestClosedError):
                await client.validate_measurement_request(
                    "01234567890",
                    {
                        "postalCode": "0000 AA",
                        "houseNumber": "1",
                        "houseNumberAddition": "",
                    },
                )


class TestClosedRequestEntities(unittest.IsolatedAsyncioTestCase):
    """Disable only the rejected meter and maintain that decision across restarts."""

    async def test_rejection_disables_entities_and_never_submits(self) -> None:
        """A single sap-01 blocks a second check and both submission actions."""
        coordinator = _coordinator()
        entry = _entry()
        coordinator.api.validate_measurement_request.side_effect = (
            PWNReadingRequestClosedError("PWN request closed")
        )
        prepare = PWNPrepareMeterReadingButton(coordinator, entry, "00012345")
        submit = PWNSubmitMeterReadingButton(coordinator, entry, "00012345")
        number_entity = PWNReadingDraftNumber(coordinator, entry, "00012345")
        date_entity = PWNReadingDate(coordinator, entry, "00012345")
        description = READING_REQUEST_SENSOR_DESCRIPTIONS[0]
        request_sensor = PWNReadingRequestSensor(
            coordinator, entry, "00012345", description
        )
        request_binary = PWNReadingRequestBinarySensor(coordinator, entry, "00012345")
        overall_binary = PWNReadingRequestBinarySensor(coordinator, entry)
        self.assertTrue(prepare.available)

        with self.assertRaisesRegex(HomeAssistantError, "no longer available"):
            await prepare.async_press()

        for entity in (
            prepare,
            submit,
            number_entity,
            date_entity,
            request_sensor,
            request_binary,
        ):
            with self.subTest(unique_id=entity.unique_id):
                self.assertFalse(entity.available)
        self.assertFalse(overall_binary.is_on)
        self.assertTrue(overall_binary.available)
        self.assertEqual(
            coordinator._closed_reading_requests["00012345"], "01234567890"
        )
        coordinator.async_save_drafts.assert_awaited()
        coordinator.api.validate_plausible_reading.assert_not_awaited()
        coordinator.api.submit_meter_reading.assert_not_awaited()
        with self.assertRaises(HomeAssistantError):
            await prepare.async_press()
        coordinator.api.validate_measurement_request.assert_awaited_once()

    async def test_sap_01_is_scoped_to_exact_request_and_meter(self) -> None:
        """Allow other meters and a new opname number without reusing approval."""
        coordinator = _coordinator()
        coordinator.api.validate_measurement_request.side_effect = (
            PWNReadingRequestClosedError("PWN request closed")
        )
        entry = _entry()
        prepare = PWNPrepareMeterReadingButton(coordinator, entry, "00012345")
        with self.assertRaises(HomeAssistantError):
            await prepare.async_press()

        original_request = coordinator.data[DATA_READING_REQUESTS][0]
        coordinator.data[DATA_READING_REQUESTS].append(
            {
                **original_request,
                "meterNumber": "00054321",
                "readingNumber": "12345678901",
            }
        )
        other_meter = PWNReadingDraftNumber(coordinator, entry, "00054321")
        self.assertTrue(other_meter.available)
        self.assertTrue(PWNReadingRequestBinarySensor(coordinator, entry).is_on)

        original_request["readingNumber"] = "22222222222"
        self.assertTrue(PWNReadingDraftNumber(coordinator, entry, "00012345").available)
        self.assertFalse(
            coordinator.is_reading_request_closed("00012345", "22222222222")
        )
        self.assertTrue(
            PWNReadingRequestBinarySensor(coordinator, entry, "00012345").available
        )
        self.assertFalse(prepare.available)

    async def test_closed_rejection_is_restored_and_corrupt_records_ignored(
        self,
    ) -> None:
        """Persist a rejection separately from confirmation and old local drafts."""
        coordinator = _coordinator()
        coordinator._closed_reading_requests["00012345"] = "01234567890"
        saved = PWNDataUpdateCoordinator._draft_storage_payload(coordinator)
        saved["closed_requests"].extend(
            [
                {"meter_number": "", "reading_number": "22222222222"},
                {"meter_number": "00054321", "reading_number": "<invalid>"},
                {"meter_number": "00054321", "reading_number": 12345678901},
            ]
        )
        restored = _coordinator()
        restored._draft_store.async_load = AsyncMock(return_value=saved)

        await PWNDataUpdateCoordinator.async_load_drafts(restored)

        self.assertEqual(restored._closed_reading_requests, {"00012345": "01234567890"})
        self.assertFalse(
            PWNReadingDraftNumber(restored, _entry(), "00012345").available
        )
        self.assertEqual(
            restored.draft_readings["00012345"],
            coordinator.draft_readings["00012345"],
        )
        self.assertEqual(restored.confirmed_readings, {})

    async def test_transient_validation_error_keeps_entity_available(self) -> None:
        """HTTP failures unrelated to sap-01 must not disable the meter."""
        coordinator = _coordinator()
        coordinator.api.validate_measurement_request.side_effect = UpdateFailed(
            "synthetic-private-upstream-response"
        )
        prepare = PWNPrepareMeterReadingButton(coordinator, _entry(), "00012345")
        with self.assertRaisesRegex(HomeAssistantError, "could not approve"):
            await prepare.async_press()
        self.assertTrue(prepare.available)
        self.assertEqual(coordinator._closed_reading_requests, {})
        coordinator.api.validate_plausible_reading.assert_not_awaited()
        coordinator.api.submit_meter_reading.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
