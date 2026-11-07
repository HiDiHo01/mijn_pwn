"""Regression tests for the two-step, explicit PWN meter-reading confirmation."""

import asyncio
from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import AsyncMock, MagicMock

from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import UpdateFailed

from .api import PWNReadingNotPlausibleError
from .button import PWNPrepareMeterReadingButton, PWNSubmitMeterReadingButton
from .const import DATA_READING_REQUESTS, DATA_USER
from .coordinator import (
    PWNDataUpdateCoordinator,
    PWNPreparedSubmission,
    PWNReadingDraft,
    PWNSubmissionDetails,
)


def _coordinator() -> MagicMock:
    """Build a mocked coordinator with real preparation and confirmation behavior."""
    coordinator = MagicMock()
    coordinator.api.validate_plausible_reading = AsyncMock(
        return_value={"isPlausible": True, "minReading": None, "maxReading": None}
    )
    coordinator.api.submit_meter_reading = AsyncMock(
        return_value={"success": True, "errorText": ""}
    )
    coordinator._submission_lock = asyncio.Lock()
    coordinator._prepared_submissions = {}
    coordinator._confirmation_cancels = {}
    coordinator.confirmed_readings = {}
    coordinator._confirmed_store.async_save = AsyncMock()
    coordinator.last_confirmed_meter_reading = None
    coordinator.draft_readings = {
        "00012345": PWNReadingDraft(
            value=165,
            entered_at=datetime(2026, 11, 7, 9, 0, tzinfo=timezone.utc),
            reading_number="01234567890",
            reading_date=datetime(2026, 11, 7, tzinfo=timezone.utc).date(),
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
                "readingStartDate": "2026-10-25T00:00:00.0000000Z",
                "readingDate": "2026-11-21T00:00:00.0000000Z",
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

    def _details(meter_number: str) -> PWNSubmissionDetails | None:
        """Extract a snapshot using production coordinator validation."""
        return PWNDataUpdateCoordinator.current_submission_details(
            coordinator, meter_number
        )

    def _is_prepared(details: PWNSubmissionDetails) -> bool:
        """Apply the production confirmation deadline and identity rules."""
        return PWNDataUpdateCoordinator.is_prepared_submission(coordinator, details)

    def _invalidate(meter_number: str) -> None:
        """Remove a pending confirmation from the synthetic coordinator."""
        PWNDataUpdateCoordinator.invalidate_prepared_submission(
            coordinator, meter_number
        )

    def _record(**kwargs: object) -> None:
        """Record only a confirmed final response using production behavior."""
        PWNDataUpdateCoordinator.record_confirmed_meter_submission(
            coordinator, **kwargs
        )

    async def _prepare(details: PWNSubmissionDetails) -> None:
        """Run production plausibility-check orchestration."""
        await PWNDataUpdateCoordinator.async_prepare_meter_reading(coordinator, details)

    async def _confirm(details: PWNSubmissionDetails) -> None:
        """Run production final-submission orchestration."""
        await PWNDataUpdateCoordinator.async_confirm_meter_reading(coordinator, details)

    coordinator._reading_date_in_window.side_effect = (
        PWNDataUpdateCoordinator._reading_date_in_window
    )
    coordinator.was_reading_submitted.side_effect = (
        lambda meter_number, reading_number: (
            PWNDataUpdateCoordinator.was_reading_submitted(
                coordinator, meter_number, reading_number
            )
        )
    )
    coordinator.current_submission_details.side_effect = _details
    coordinator.is_prepared_submission.side_effect = _is_prepared
    coordinator.invalidate_prepared_submission.side_effect = _invalidate
    coordinator.record_confirmed_meter_submission.side_effect = _record
    coordinator.async_prepare_meter_reading = AsyncMock(side_effect=_prepare)
    coordinator.async_confirm_meter_reading = AsyncMock(side_effect=_confirm)
    return coordinator


def _buttons(
    coordinator: MagicMock,
) -> tuple[PWNPrepareMeterReadingButton, PWNSubmitMeterReadingButton]:
    """Build both actions for the same fictional water meter."""
    entry = MagicMock()
    entry.unique_id = "fictional-account"
    entry.entry_id = "fictional-entry"
    return (
        PWNPrepareMeterReadingButton(coordinator, entry, "00012345"),
        PWNSubmitMeterReadingButton(coordinator, entry, "00012345"),
    )


class TestSubmissionWorkflow(unittest.IsolatedAsyncioTestCase):
    """Ensure each water meter reading requires a fresh explicit confirmation."""

    async def test_first_button_checks_only_second_button_submits(self) -> None:
        """Never submit a reading as a side effect of its plausibility check."""
        coordinator = _coordinator()
        check_button, confirm_button = _buttons(coordinator)
        self.assertTrue(check_button.available)
        self.assertFalse(confirm_button.available)
        coordinator.api.submit_meter_reading.assert_not_awaited()

        await check_button.async_press()

        coordinator.api.validate_plausible_reading.assert_awaited_once()
        coordinator.api.submit_meter_reading.assert_not_awaited()
        self.assertTrue(confirm_button.available)
        self.assertIsNone(coordinator.last_confirmed_meter_reading)

        await confirm_button.async_press()

        coordinator.api.submit_meter_reading.assert_awaited_once()
        self.assertEqual(coordinator.last_confirmed_meter_reading.value, 165)
        self.assertEqual(
            coordinator.last_confirmed_meter_reading.reading_number,
            "01234567890",
        )
        self.assertFalse(confirm_button.available)
        self.assertFalse(check_button.available)
        self.assertEqual(coordinator._prepared_submissions, {})

    async def test_confirmation_without_prior_check_is_rejected(self) -> None:
        """A single press of the old submission button cannot submit directly."""
        coordinator = _coordinator()
        _, confirm_button = _buttons(coordinator)

        with self.assertRaisesRegex(HomeAssistantError, "First check"):
            await confirm_button.async_press()

        coordinator.api.validate_plausible_reading.assert_not_awaited()
        coordinator.api.submit_meter_reading.assert_not_awaited()

    async def test_expired_confirmation_requires_new_check(self) -> None:
        """Do not submit the old draft after the two-minute window expires."""
        coordinator = _coordinator()
        check_button, confirm_button = _buttons(coordinator)
        await check_button.async_press()
        approved = coordinator._prepared_submissions["00012345"]
        coordinator._prepared_submissions["00012345"] = PWNPreparedSubmission(
            details=approved.details,
            expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
        )

        self.assertFalse(confirm_button.available)
        with self.assertRaisesRegex(HomeAssistantError, "within two minutes"):
            await confirm_button.async_press()
        coordinator.api.submit_meter_reading.assert_not_awaited()

        await check_button.async_press()
        self.assertTrue(confirm_button.available)

    async def test_modified_draft_revokes_confirmation(self) -> None:
        """An edited value or entry timestamp must invalidate previous approval."""
        coordinator = _coordinator()
        check_button, confirm_button = _buttons(coordinator)
        await check_button.async_press()
        old_draft = coordinator.draft_readings["00012345"]
        coordinator.draft_readings["00012345"] = PWNReadingDraft(
            value=166,
            entered_at=old_draft.entered_at + timedelta(seconds=1),
            reading_number=old_draft.reading_number,
            reading_date=old_draft.reading_date,
        )
        self.assertFalse(confirm_button.available)
        with self.assertRaises(HomeAssistantError):
            await confirm_button.async_press()
        coordinator.api.submit_meter_reading.assert_not_awaited()

    async def test_changed_request_or_address_invalidates_confirmation(self) -> None:
        """A new opname number or changed connection address requires reapproval."""
        coordinator = _coordinator()
        check_button, confirm_button = _buttons(coordinator)
        await check_button.async_press()
        coordinator.data[DATA_READING_REQUESTS][0]["address"]["houseNumber"] = "2"

        self.assertFalse(confirm_button.available)
        with self.assertRaises(HomeAssistantError):
            await confirm_button.async_press()
        coordinator.api.submit_meter_reading.assert_not_awaited()

    async def test_false_plausibility_cannot_arm_confirmation(self) -> None:
        """Do not reveal a confirmation action when PWN rejects the reading."""
        coordinator = _coordinator()
        check_button, confirm_button = _buttons(coordinator)
        coordinator.api.validate_plausible_reading.side_effect = (
            PWNReadingNotPlausibleError(160, 170)
        )
        with self.assertRaisesRegex(HomeAssistantError, "implausible"):
            await check_button.async_press()
        self.assertFalse(confirm_button.available)
        self.assertEqual(coordinator._prepared_submissions, {})
        coordinator.api.submit_meter_reading.assert_not_awaited()

    async def test_request_change_during_check_never_arms_confirmation(self) -> None:
        """Discard approval if the draft changes while awaiting the PWN API."""
        coordinator = _coordinator()
        check_button, confirm_button = _buttons(coordinator)

        async def _modify_during_check(*args: object) -> dict[str, object]:
            """Simulate editing the draft during the remote plausibility call."""
            old_draft = coordinator.draft_readings["00012345"]
            coordinator.draft_readings["00012345"] = PWNReadingDraft(
                value=170,
                entered_at=old_draft.entered_at,
                reading_number=old_draft.reading_number,
            )
            return {"isPlausible": True, "minReading": None, "maxReading": None}

        coordinator.api.validate_plausible_reading.side_effect = _modify_during_check
        with self.assertRaisesRegex(HomeAssistantError, "could not approve"):
            await check_button.async_press()
        self.assertFalse(confirm_button.available)
        coordinator.api.submit_meter_reading.assert_not_awaited()

    async def test_final_post_failure_preserves_confirmed_and_disarms(self) -> None:
        """Do not update previously confirmed sensors on a failed final POST."""
        coordinator = _coordinator()
        check_button, confirm_button = _buttons(coordinator)
        await check_button.async_press()
        coordinator.api.submit_meter_reading.side_effect = UpdateFailed(
            "fictional-private-upstream-error"
        )

        with self.assertRaisesRegex(HomeAssistantError, "not confirmed"):
            await confirm_button.async_press()

        self.assertIsNone(coordinator.last_confirmed_meter_reading)
        self.assertEqual(coordinator._prepared_submissions, {})
        self.assertTrue(check_button.available)
        self.assertFalse(confirm_button.available)

    async def test_second_confirm_press_cannot_repeat_final_post(self) -> None:
        """A completed confirmation cannot be reused even after an API timeout."""
        coordinator = _coordinator()
        check_button, confirm_button = _buttons(coordinator)
        await check_button.async_press()
        await confirm_button.async_press()

        with self.assertRaises(HomeAssistantError):
            await confirm_button.async_press()
        self.assertEqual(coordinator.api.submit_meter_reading.await_count, 1)

    async def test_check_and_confirm_ids_remain_distinct(self) -> None:
        """Keep a stable existing confirmation ID and add a separate check ID."""
        coordinator = _coordinator()
        check_button, confirm_button = _buttons(coordinator)
        self.assertEqual(
            check_button.unique_id,
            "fictional-account_reading_meter_00012345_prepare_meter_reading",
        )
        self.assertEqual(
            confirm_button.unique_id,
            "fictional-account_reading_meter_00012345_submit_meter_reading",
        )

    async def test_new_draft_requires_another_confirmation(self) -> None:
        """Clearing a previous approval must block a new edited value."""
        coordinator = _coordinator()
        check_button, confirm_button = _buttons(coordinator)
        await check_button.async_press()
        coordinator.invalidate_prepared_submission("00012345")
        self.assertFalse(confirm_button.available)
        coordinator.api.submit_meter_reading.assert_not_awaited()
