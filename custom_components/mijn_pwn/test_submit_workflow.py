"""Verify explicit meter submission action, ordering, and failure isolation."""

import asyncio
from datetime import date, datetime, timezone
import unittest
from unittest.mock import AsyncMock, MagicMock

from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import UpdateFailed

from .api import PWNReadingNotPlausibleError
from .button import PWNSubmitMeterReadingButton
from .const import DATA_READING_REQUESTS, DATA_USER
from .coordinator import PWNDataUpdateCoordinator, PWNReadingDraft


def _coordinator() -> MagicMock:
    """Build a coordinator with synthetic account data and no network."""
    coordinator = MagicMock()
    coordinator.api.validate_plausible_reading = AsyncMock(
        return_value={"isPlausible": True, "minReading": None, "maxReading": None}
    )
    coordinator.api.submit_meter_reading = AsyncMock(
        return_value={"success": True, "errorText": ""}
    )
    coordinator._submission_lock = asyncio.Lock()
    coordinator.last_confirmed_meter_reading = None
    coordinator.draft_readings = {
        "00012345": PWNReadingDraft(
            value=165,
            entered_at=datetime(2026, 11, 7, 9, 0, tzinfo=timezone.utc),
            reading_number="01234567890",
        )
    }
    coordinator.data = {
        DATA_USER: {"relationNumber": "0012345"},
        DATA_READING_REQUESTS: [
            {
                "openReading": True,
                "readingNumber": "01234567890",
                "amountOfWheels": 5,
                "meterNumber": "00012345",
                "address": {
                    "postalCode": "0000 AA",
                    "street": "Synthetic Lane",
                    "houseNumber": "1",
                    "houseNumberAddition": "",
                },
            }
        ],
    }
    coordinator.last_update_success = True

    def _record(**kwargs: object) -> None:
        """Apply the production state transition to the synthetic coordinator."""
        PWNDataUpdateCoordinator.record_confirmed_meter_submission(
            coordinator, **kwargs
        )

    coordinator.record_confirmed_meter_submission = _record
    return coordinator


def _button(coordinator: MagicMock) -> PWNSubmitMeterReadingButton:
    """Create an explicit button for the fictional water meter."""
    entry = MagicMock()
    entry.unique_id = "fictional-account"
    entry.entry_id = "fictional-entry"
    return PWNSubmitMeterReadingButton(coordinator, entry, "00012345")


class TestSubmissionWorkflow(unittest.IsolatedAsyncioTestCase):
    """Ensure only final server acknowledgement changes confirmed reading."""

    @staticmethod
    def _submit(coordinator: MagicMock):
        """Run the actual submission orchestration with fictional arguments."""
        return PWNDataUpdateCoordinator.async_submit_meter_reading(
            coordinator,
            reading_number="01234567890",
            relation_number="0012345",
            meter_number="00012345",
            meter_reading=165,
            wheel_count=5,
            reading_date=date(2026, 11, 7),
            address=coordinator.data[DATA_READING_REQUESTS][0]["address"],
        )

    async def test_check_then_submit_then_store_confirmation(self) -> None:
        """Record state only when plausibility and final POST both succeed."""
        coordinator = _coordinator()
        assert coordinator.last_confirmed_meter_reading is None

        await self._submit(coordinator)

        coordinator.api.validate_plausible_reading.assert_awaited_once()
        coordinator.api.submit_meter_reading.assert_awaited_once()
        self.assertEqual(coordinator.last_confirmed_meter_reading.value, 165)
        self.assertEqual(
            coordinator.last_confirmed_meter_reading.reading_number,
            "01234567890",
        )
        coordinator.async_update_listeners.assert_called_once_with()

        with self.assertRaisesRegex(ValueError, "already submitted"):
            await self._submit(coordinator)
        self.assertEqual(coordinator.api.submit_meter_reading.await_count, 1)

    async def test_implausible_never_calls_final_post(self) -> None:
        """Stop the transaction before submission on a rejected plausibility flag."""
        coordinator = _coordinator()
        coordinator.api.validate_plausible_reading.side_effect = (
            PWNReadingNotPlausibleError(160, 170)
        )
        with self.assertRaises(PWNReadingNotPlausibleError):
            await self._submit(coordinator)
        coordinator.api.submit_meter_reading.assert_not_awaited()
        self.assertIsNone(coordinator.last_confirmed_meter_reading)
        coordinator.async_update_listeners.assert_not_called()

    async def test_failed_final_request_preserves_confirmed_value(self) -> None:
        """Do not replace previous confirmed sensor data on a rejected final POST."""
        coordinator = _coordinator()
        await self._submit(coordinator)
        previous = coordinator.last_confirmed_meter_reading
        coordinator.api.submit_meter_reading.side_effect = UpdateFailed(
            "synthetic private response"
        )
        coordinator.async_update_listeners.reset_mock()

        with self.assertRaises(UpdateFailed):
            await PWNDataUpdateCoordinator.async_submit_meter_reading(
                coordinator,
                reading_number="12345678901",
                relation_number="0012345",
                meter_number="00012345",
                meter_reading=170,
                wheel_count=5,
                reading_date=date(2026, 11, 7),
                address=coordinator.data[DATA_READING_REQUESTS][0]["address"],
            )

        self.assertIs(coordinator.last_confirmed_meter_reading, previous)
        coordinator.async_update_listeners.assert_not_called()

    async def test_button_requires_matching_draft(self) -> None:
        """Prevent accidental submission without an open draft or relation number."""
        coordinator = _coordinator()
        button = _button(coordinator)
        self.assertTrue(button.available)
        coordinator.draft_readings.clear()
        self.assertFalse(button.available)
        with self.assertRaises(HomeAssistantError):
            await button.async_press()
        coordinator.api.validate_plausible_reading.assert_not_awaited()

    async def test_button_submits_draft_only_on_explicit_press(self) -> None:
        """Use the entered reading date and never send when only number changes."""
        coordinator = _coordinator()
        coordinator.async_submit_meter_reading = AsyncMock(return_value=None)
        button = _button(coordinator)
        self.assertEqual(
            button.unique_id,
            "fictional-account_reading_meter_00012345_submit_meter_reading",
        )
        coordinator.async_submit_meter_reading.assert_not_awaited()

        await button.async_press()

        coordinator.async_submit_meter_reading.assert_awaited_once()
        args = coordinator.async_submit_meter_reading.await_args.kwargs
        self.assertEqual(args["reading_number"], "01234567890")
        self.assertEqual(args["relation_number"], "0012345")
        self.assertEqual(args["meter_reading"], 165)
        self.assertEqual(args["reading_date"], date(2026, 11, 7))

    async def test_button_error_is_safe_and_keeps_draft(self) -> None:
        """Leave the draft and confirmed states untouched when PWN rejects."""
        coordinator = _coordinator()
        coordinator.async_submit_meter_reading = AsyncMock(
            side_effect=PWNReadingNotPlausibleError(None, None)
        )
        button = _button(coordinator)
        with self.assertRaisesRegex(HomeAssistantError, "implausible"):
            await button.async_press()
        self.assertIsNotNone(coordinator.draft_readings.get("00012345"))
        self.assertIsNone(coordinator.last_confirmed_meter_reading)
