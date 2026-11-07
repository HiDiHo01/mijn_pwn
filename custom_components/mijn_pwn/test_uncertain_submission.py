"""Offline regressions for ambiguous PWN final meter submissions."""

import asyncio
from datetime import datetime, timezone
import unittest
from unittest.mock import AsyncMock, MagicMock

from homeassistant.helpers.update_coordinator import UpdateFailed

from .button import PWNPrepareMeterReadingButton, PWNSubmitMeterReadingButton
from .coordinator import PWNDataUpdateCoordinator, PWNReadingDraft
from .test_submit_workflow import _buttons, _coordinator


class TestUncertainSubmission(unittest.IsolatedAsyncioTestCase):
    """Protect submitted requests through failure, restart, and concurrency."""

    async def test_timeout_after_possible_processing_is_durable(self) -> None:
        """Save the unknown outcome before posting and prevent a second POST."""
        coordinator = _coordinator()
        prepare, submit = _buttons(coordinator)
        await prepare.async_press()

        async def _lost_response(*args: object) -> None:
            """Simulate a processed POST whose HTTP reply was lost."""
            saved = coordinator._confirmed_store.async_save.await_args.args[0]
            self.assertEqual(len(saved["unresolved_submissions"]), 1)
            self.assertEqual(saved["readings"], [])
            raise UpdateFailed("synthetic timeout")

        coordinator.api.submit_meter_reading.side_effect = _lost_response
        details = coordinator.current_submission_details("00012345")
        with self.assertRaises(UpdateFailed):
            await coordinator.async_confirm_meter_reading(details)

        self.assertFalse(prepare.available)
        self.assertFalse(submit.available)
        self.assertIsNone(coordinator.last_confirmed_meter_reading)
        self.assertEqual(coordinator.api.submit_meter_reading.await_count, 1)
        with self.assertRaisesRegex(ValueError, "outcome unknown"):
            await coordinator.async_prepare_meter_reading(details)
        coordinator.api.submit_meter_reading.assert_awaited_once()
        coordinator._create_uncertain_repair.assert_called_once_with(
            "00012345", "01234567890"
        )

    async def test_restart_during_submission_restores_block(self) -> None:
        """A write-ahead marker remains after a crash before acknowledgement."""
        original = _coordinator()
        original.uncertain_submissions["00012345"] = {
            "01234567890": datetime(2026, 11, 7, 9, tzinfo=timezone.utc)
        }
        saved = PWNDataUpdateCoordinator._confirmed_storage_payload(original)
        restored = _coordinator()
        restored._confirmed_store.async_load = AsyncMock(return_value=saved)
        await PWNDataUpdateCoordinator.async_load_confirmed_readings(restored)
        self.assertTrue(restored.is_submission_uncertain("00012345", "01234567890"))
        self.assertFalse(_buttons(restored)[0].available)
        restored._create_uncertain_repair.assert_called_once_with(
            "00012345", "01234567890"
        )
        restored.api.submit_meter_reading.assert_not_awaited()

    async def test_storage_failure_never_sends_post(self) -> None:
        """A failed durable write must abort before sending anything."""
        coordinator = _coordinator()
        prepare, submit = _buttons(coordinator)
        await prepare.async_press()
        coordinator._confirmed_store.async_save.side_effect = OSError(
            "synthetic storage failure"
        )
        with self.assertRaises(OSError):
            await coordinator.async_confirm_meter_reading(
                coordinator.current_submission_details("00012345")
            )
        coordinator.api.submit_meter_reading.assert_not_awaited()
        self.assertFalse(coordinator.uncertain_submissions)
        coordinator._create_uncertain_repair.assert_not_called()
        self.assertFalse(submit.available)

    async def test_concurrent_confirmations_send_only_once(self) -> None:
        """The submission lock prevents two POSTs after a lost response."""
        coordinator = _coordinator()
        prepare, _ = _buttons(coordinator)
        await prepare.async_press()
        details = coordinator.current_submission_details("00012345")
        entered = asyncio.Event()
        release = asyncio.Event()

        async def _timeout(*args: object) -> None:
            """Pause while another confirmation starts."""
            entered.set()
            await release.wait()
            raise UpdateFailed("synthetic timeout")

        coordinator.api.submit_meter_reading.side_effect = _timeout
        first = asyncio.create_task(coordinator.async_confirm_meter_reading(details))
        await entered.wait()
        second = asyncio.create_task(coordinator.async_confirm_meter_reading(details))
        release.set()
        results = await asyncio.gather(first, second, return_exceptions=True)
        self.assertIsInstance(results[0], UpdateFailed)
        self.assertIsInstance(results[1], ValueError)
        self.assertEqual(coordinator.api.submit_meter_reading.await_count, 1)
        self.assertTrue(coordinator.is_submission_uncertain("00012345", "01234567890"))

    async def test_manual_release_requires_exact_request_and_saved_state(self) -> None:
        """Do not release the wrong ID or lose a marker when saving fails."""
        coordinator = _coordinator()
        coordinator.uncertain_submissions["00012345"] = {
            "01234567890": datetime.now(timezone.utc)
        }
        with self.assertRaises(ValueError):
            await coordinator.async_release_uncertain_submission(
                "00012345", "22222222222"
            )
        coordinator._confirmed_store.async_save.side_effect = OSError(
            "synthetic disk failure"
        )
        with self.assertRaises(OSError):
            await coordinator.async_release_uncertain_submission(
                "00012345", "01234567890"
            )
        self.assertTrue(coordinator.is_submission_uncertain("00012345", "01234567890"))
        coordinator._delete_uncertain_repair.assert_not_called()

        coordinator._confirmed_store.async_save.side_effect = None
        await coordinator.async_release_uncertain_submission(
            "00012345", "01234567890"
        )
        self.assertFalse(coordinator.is_submission_uncertain("00012345", "01234567890"))
        coordinator._delete_uncertain_repair.assert_called_once_with(
            "00012345", "01234567890"
        )
        self.assertTrue(_buttons(coordinator)[0].available)

    async def test_new_request_id_is_independent(self) -> None:
        """An unresolved older ID cannot prevent a distinct new request."""
        coordinator = _coordinator()
        coordinator.uncertain_submissions["00012345"] = {
            "01234567890": datetime.now(timezone.utc)
        }
        original = coordinator.draft_readings["00012345"]
        coordinator.data["reading_requests"][0]["readingNumber"] = "22222222222"
        coordinator.draft_readings["00012345"] = PWNReadingDraft(
            value=original.value,
            entered_at=original.entered_at,
            reading_number="22222222222",
            reading_date=original.reading_date,
        )
        entry = MagicMock(unique_id="synthetic", entry_id="synthetic-entry")
        prepare = PWNPrepareMeterReadingButton(coordinator, entry, "00012345")
        self.assertTrue(prepare.available)
        await prepare.async_press()
        self.assertTrue(
            PWNSubmitMeterReadingButton(coordinator, entry, "00012345").available
        )
        self.assertTrue(coordinator.is_submission_uncertain("00012345", "01234567890"))
        coordinator.api.submit_meter_reading.assert_not_awaited()
