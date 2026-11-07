"""Regression coverage for PWN meter submission review corrections."""

import asyncio
from copy import deepcopy
from dataclasses import replace
from datetime import date, datetime, timezone
from inspect import iscoroutinefunction
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.exceptions import HomeAssistantError

from . import button, date as date_platform, number
from .button import PWNPrepareMeterReadingButton
from .const import DATA_READING_REQUESTS, DATA_USER
from .coordinator import PWNDataUpdateCoordinator, PWNReadingDraft
from .date import PWNReadingDate
from .number import PWNReadingDraftNumber
from .test_submit_workflow import _coordinator


class TestPlatformSetupContracts(unittest.TestCase):
    """Keep the Home Assistant coroutine platform contract intact."""

    def test_platform_entrypoints_are_coroutines(self) -> None:
        """Home Assistant awaits each platform even if entity registration is sync."""
        for platform in (button, date_platform, number):
            with self.subTest(platform=platform.__name__):
                self.assertTrue(iscoroutinefunction(platform.async_setup_entry))


class TestMeterDateInput(unittest.IsolatedAsyncioTestCase):
    """Ensure the user explicitly selects the original observation date."""

    @staticmethod
    def _date_entity(coordinator: MagicMock) -> PWNReadingDate:
        """Create the date selector for a fictional water meter."""
        entry = MagicMock()
        entry.unique_id = "fictional-account"
        entry.entry_id = "fictional-entry"
        return PWNReadingDate(coordinator, entry, "00012345")

    async def test_user_date_is_independent_of_entry_time(self) -> None:
        """Use the chosen observation date rather than the draft creation date."""
        coordinator = _coordinator()
        entity = self._date_entity(coordinator)
        self.assertTrue(entity.available)
        await entity.async_set_value(date(2026, 11, 5))
        details = coordinator.current_submission_details("00012345")
        self.assertIsNotNone(details)
        self.assertEqual(details.reading_date, date(2026, 11, 5))
        self.assertEqual(entity.native_value, date(2026, 11, 5))
        self.assertEqual(
            coordinator.draft_readings["00012345"].entered_at,
            datetime(2026, 11, 7, 9, 0, tzinfo=timezone.utc),
        )

    async def test_date_outside_request_is_rejected(self) -> None:
        """Do not accept a date before the PWN start or after its deadline."""
        coordinator = _coordinator()
        entity = self._date_entity(coordinator)
        for reading_date in (
            date(2026, 10, 24),
            date(2026, 11, 22),
            datetime(2026, 11, 7, tzinfo=timezone.utc),
        ):
            with self.subTest(reading_date=reading_date):
                with self.assertRaises(HomeAssistantError):
                    await entity.async_set_value(reading_date)
        self.assertEqual(entity.native_value, date(2026, 11, 7))

    async def test_missing_date_prevents_preparation(self) -> None:
        """A blank observation date must prevent either network call."""
        coordinator = _coordinator()
        draft = coordinator.draft_readings["00012345"]
        coordinator.draft_readings["00012345"] = replace(draft, reading_date=None)
        self.assertIsNone(coordinator.current_submission_details("00012345"))
        entry = MagicMock()
        entry.unique_id = "fictional-account"
        entry.entry_id = "fictional-entry"
        button = PWNPrepareMeterReadingButton(coordinator, entry, "00012345")
        self.assertFalse(button.available)
        with self.assertRaises(HomeAssistantError):
            await button.async_press()
        coordinator.api.validate_plausible_reading.assert_not_awaited()
        coordinator.api.submit_meter_reading.assert_not_awaited()

    async def test_date_change_revokes_prior_approval(self) -> None:
        """Changing the observation date invalidates a prepared submission."""
        coordinator = _coordinator()
        details = coordinator.current_submission_details("00012345")
        await coordinator.async_prepare_meter_reading(details)
        self.assertTrue(coordinator.is_prepared_submission(details))

        await self._date_entity(coordinator).async_set_value(date(2026, 11, 6))

        self.assertFalse(coordinator.is_prepared_submission(details))
        self.assertEqual(coordinator._prepared_submissions, {})
        coordinator.api.submit_meter_reading.assert_not_awaited()


class TestPerMeterSubmissionHistory(unittest.IsolatedAsyncioTestCase):
    """Protect every confirmed meter request across meters and restarts."""

    @staticmethod
    def _add_second_meter(coordinator: MagicMock) -> None:
        """Create a second synthetic meter with its own open request."""
        another_request = {
            **coordinator.data[DATA_READING_REQUESTS][0],
            "meterNumber": "00054321",
            "readingNumber": "12345678901",
        }
        coordinator.data[DATA_READING_REQUESTS].append(another_request)
        coordinator.draft_readings["00054321"] = PWNReadingDraft(
            value=171,
            entered_at=datetime(2026, 11, 7, 9, 0, tzinfo=timezone.utc),
            reading_number="12345678901",
            reading_date=date(2026, 11, 7),
        )

    async def test_two_meters_never_lose_duplicate_protection(self) -> None:
        """Submitting B must not make the earlier request on A reusable."""
        coordinator = _coordinator()
        self._add_second_meter(coordinator)

        previous_details = None
        for meter_number in ("00012345", "00054321"):
            details = coordinator.current_submission_details(meter_number)
            self.assertIsNotNone(details)
            if meter_number == "00012345":
                previous_details = details
            await coordinator.async_prepare_meter_reading(details)
            await coordinator.async_confirm_meter_reading(details)

        self.assertEqual(coordinator.api.submit_meter_reading.await_count, 2)
        self.assertEqual(len(coordinator.confirmed_readings), 2)
        self.assertTrue(coordinator.was_reading_submitted("00012345", "01234567890"))
        self.assertTrue(coordinator.was_reading_submitted("00054321", "12345678901"))

        self.assertIsNone(coordinator.current_submission_details("00012345"))
        self.assertIsNotNone(previous_details)
        with self.assertRaisesRegex(ValueError, "Meter reading changed"):
            await coordinator.async_prepare_meter_reading(previous_details)
        self.assertEqual(coordinator.api.submit_meter_reading.await_count, 2)
        coordinator._confirmed_store.async_save.assert_awaited()
        saved = coordinator._confirmed_store.async_save.await_args.args[0]
        self.assertEqual(len(saved["readings"]), 2)

    async def test_restore_multiple_meters_before_any_entities(self) -> None:
        """Reload all accepted requests rather than only the latest one."""
        coordinator = _coordinator()
        self._add_second_meter(coordinator)
        for meter_number in ("00012345", "00054321"):
            details = coordinator.current_submission_details(meter_number)
            await coordinator.async_prepare_meter_reading(details)
            await coordinator.async_confirm_meter_reading(details)

        saved = coordinator._confirmed_storage_payload()
        reloaded = _coordinator()
        reloaded._confirmed_store.async_load = AsyncMock(return_value=saved)
        reloaded.confirmed_readings.clear()
        reloaded.last_confirmed_meter_reading = None

        await PWNDataUpdateCoordinator.async_load_confirmed_readings(reloaded)

        self.assertTrue(reloaded.was_reading_submitted("00012345", "01234567890"))
        self.assertTrue(reloaded.was_reading_submitted("00054321", "12345678901"))
        self.assertIsNotNone(reloaded.last_confirmed_meter_reading)
        self.assertEqual(reloaded.last_confirmed_meter_reading.meter_number, "00054321")

    async def test_invalid_persisted_records_are_ignored(self) -> None:
        """Corrupt or unauthenticated records cannot become valid confirmations."""
        coordinator = _coordinator()
        coordinator._confirmed_store.async_load = AsyncMock(
            return_value={
                "readings": [
                    {
                        "meter_number": "00012345",
                        "reading_number": "invalid",
                        "value": 12,
                        "wheel_count": 5,
                        "confirmed_at": "2026-11-07T09:01:00+00:00",
                    }
                ]
            }
        )
        await PWNDataUpdateCoordinator.async_load_confirmed_readings(coordinator)
        self.assertEqual(coordinator.confirmed_readings, {})
        self.assertIsNone(coordinator.last_confirmed_meter_reading)


class TestSubmissionValidation(unittest.TestCase):
    """Check independently validated identifiers and submission details."""

    def test_ambiguous_open_meter_requests_are_rejected(self) -> None:
        """Never guess which of two matching requests should be submitted."""
        coordinator = _coordinator()
        requests = coordinator.data[DATA_READING_REQUESTS]
        self.assertIsNotNone(coordinator.current_submission_details("00012345"))
        requests.append({**requests[0]})

        self.assertIsNone(coordinator.current_submission_details("00012345"))

    def test_invalid_relation_number_blocks_submission(self) -> None:
        """Require exactly seven digits or the observed ten-digit prefix."""
        coordinator = _coordinator()
        account = coordinator.data[DATA_USER]
        for relation_number in ("0", "000123", "00012345678", "12345/7", 1234567):
            with self.subTest(value=relation_number):
                account["relationNumber"] = relation_number
                self.assertIsNone(coordinator.current_submission_details("00012345"))
        account["relationNumber"] = "0000012345"
        self.assertIsNotNone(coordinator.current_submission_details("00012345"))

    def test_invalid_meter_address_blocks_submission(self) -> None:
        """Reject empty or unsupported address types before posting to PWN."""
        coordinator = _coordinator()
        address = coordinator.data[DATA_READING_REQUESTS][0]["address"]
        for key, value in (
            ("postalCode", ""),
            ("street", ""),
            ("houseNumber", 1),
            ("houseNumberAddition", None),
        ):
            with self.subTest(field=key):
                original = address[key]
                address[key] = value
                self.assertIsNone(coordinator.current_submission_details("00012345"))
                address[key] = original

    def test_wheel_count_and_recorded_date_must_be_valid(self) -> None:
        """Keep invalid wheel counts and missing dates out of API requests."""
        coordinator = _coordinator()
        request = coordinator.data[DATA_READING_REQUESTS][0]
        request["amountOfWheels"] = True
        self.assertIsNone(coordinator.current_submission_details("00012345"))
        request["amountOfWheels"] = 5

        draft = coordinator.draft_readings["00012345"]
        coordinator.draft_readings["00012345"] = replace(draft, reading_date=None)
        self.assertIsNone(coordinator.current_submission_details("00012345"))


class TestConfirmationExpiry(unittest.IsolatedAsyncioTestCase):
    """Refresh Home Assistant button availability when the timer expires."""

    async def test_timer_invokes_listener_and_cleans_up(self) -> None:
        """A scheduled expiry revokes approval without needing another poll."""
        coordinator = _coordinator()
        details = coordinator.current_submission_details("00012345")
        await coordinator.async_prepare_meter_reading(details)
        coordinator._schedule_confirmation_expiry.assert_called_once()
        deadline = coordinator._prepared_submissions["00012345"].expires_at
        cancellation = MagicMock()

        with patch(
            "custom_components.mijn_pwn.coordinator.async_track_point_in_utc_time",
            return_value=cancellation,
        ) as track:
            PWNDataUpdateCoordinator._schedule_confirmation_expiry(
                coordinator, "00012345", deadline
            )
            callback = track.call_args.args[1]
            callback(deadline)

        self.assertEqual(coordinator._prepared_submissions, {})
        self.assertFalse(coordinator.is_prepared_submission(details))
        coordinator.async_update_listeners.assert_called()

    async def test_revoke_cancels_existing_timer(self) -> None:
        """Editing the draft must unsubscribe the pending expiry callback."""
        coordinator = _coordinator()
        timer_cancel = MagicMock()
        coordinator._confirmation_cancels["00012345"] = timer_cancel
        coordinator._prepared_submissions["00012345"] = MagicMock()

        coordinator.invalidate_prepared_submission("00012345")

        timer_cancel.assert_called_once_with()
        self.assertEqual(coordinator._confirmation_cancels, {})
        self.assertEqual(coordinator._prepared_submissions, {})


class TestSubmissionReviewLifecycle(unittest.IsolatedAsyncioTestCase):
    """Guard prepared submissions against refreshing data and unloading."""

    async def test_address_changed_then_restored_requires_new_check(self) -> None:
        """Revoke an approval as soon as a refresh shows changed meter details."""
        coordinator = _coordinator()
        details = coordinator.current_submission_details("00012345")
        await coordinator.async_prepare_meter_reading(details)

        updated_data = deepcopy(coordinator.data)
        updated_data[DATA_READING_REQUESTS][0]["address"]["houseNumber"] = "2"
        PWNDataUpdateCoordinator._invalidate_changed_preparations(
            coordinator, updated_data
        )

        self.assertEqual(coordinator._prepared_submissions, {})
        self.assertFalse(coordinator.is_prepared_submission(details))
        restored_data = deepcopy(coordinator.data)
        PWNDataUpdateCoordinator._invalidate_changed_preparations(
            coordinator, restored_data
        )
        self.assertFalse(coordinator.is_prepared_submission(details))
        coordinator.api.submit_meter_reading.assert_not_awaited()

    async def test_unload_waits_for_final_api_and_storage(self) -> None:
        """Do not unload platforms while a final acknowledgement is in-flight."""
        coordinator = _coordinator()
        details = coordinator.current_submission_details("00012345")
        await coordinator.async_prepare_meter_reading(details)
        entered_post = asyncio.Event()
        release_post = asyncio.Event()

        async def _slow_response(
            *_args: object, **_kwargs: object
        ) -> dict[str, object]:
            """Simulate PWN processing a write while an unload is requested."""
            entered_post.set()
            await release_post.wait()
            return {"success": True, "errorText": ""}

        coordinator.api.submit_meter_reading.side_effect = _slow_response
        confirmation = asyncio.create_task(
            coordinator.async_confirm_meter_reading(details)
        )
        await asyncio.wait_for(entered_post.wait(), timeout=2)
        unload = asyncio.create_task(
            PWNDataUpdateCoordinator.async_prepare_for_unload(coordinator)
        )
        await asyncio.sleep(0)
        self.assertTrue(coordinator._unloading)
        self.assertFalse(unload.done())

        release_post.set()
        await confirmation
        await unload
        self.assertEqual(coordinator.last_confirmed_meter_reading.value, 165)
        saves = coordinator._confirmed_store.async_save.await_args_list
        self.assertEqual(len(saves), 2)
        self.assertEqual(
            saves[0].args[0]["unresolved_submissions"][0]["reading_number"],
            "01234567890",
        )
        self.assertEqual(saves[0].args[0]["readings"], [])
        self.assertEqual(saves[1].args[0]["unresolved_submissions"], [])
        self.assertEqual(len(saves[1].args[0]["readings"]), 1)
        coordinator.async_save_drafts.assert_awaited_once()
        with self.assertRaisesRegex(ValueError, "unloading"):
            await coordinator.async_prepare_meter_reading(details)

    async def test_draft_store_survives_unavailable_request(self) -> None:
        """Use the independent store when an unavailable state loses metadata."""
        coordinator = _coordinator()
        payload = PWNDataUpdateCoordinator._draft_storage_payload(coordinator)
        self.assertEqual(payload["drafts"][0]["value"], 165)
        self.assertEqual(payload["drafts"][0]["reading_number"], "01234567890")
        self.assertEqual(payload["drafts"][0]["reading_date"], "2026-11-07")

        coordinator.data[DATA_READING_REQUESTS] = None
        self.assertIsNone(coordinator.current_submission_details("00012345"))
        reloaded = _coordinator()
        reloaded.draft_readings.clear()
        reloaded._draft_store.async_load = AsyncMock(return_value=payload)
        await PWNDataUpdateCoordinator.async_load_drafts(reloaded)

        restored = reloaded.draft_readings["00012345"]
        self.assertEqual(restored.value, 165)
        self.assertEqual(restored.reading_date, date(2026, 11, 7))

        entry = MagicMock(unique_id="fictional-account", entry_id="fictional-entry")
        number = PWNReadingDraftNumber(reloaded, entry, "00012345")
        self.assertEqual(number.native_value, 165.0)

    async def test_invalid_draft_store_records_are_ignored(self) -> None:
        """Discard corrupted timestamps and identifiers before restoring a draft."""
        coordinator = _coordinator()
        coordinator._draft_store.async_load = AsyncMock(
            return_value={
                "drafts": [
                    {
                        "meter_number": "00012345",
                        "value": 165,
                        "reading_number": "invalid",
                        "entered_at": "2026-11-07T09:00:00+00:00",
                        "reading_date": "2026-11-07",
                    }
                ]
            }
        )
        await PWNDataUpdateCoordinator.async_load_drafts(coordinator)
        self.assertEqual(coordinator.draft_readings, {})
