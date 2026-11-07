"""Offline regressions for request states after an acknowledged meter submission."""

import asyncio
from datetime import datetime, timezone
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import BaseCoordinatorEntity

from .button import PWNPrepareMeterReadingButton, PWNSubmitMeterReadingButton
from .const import DATA_READING_REQUESTS
from .coordinator import PWNDataUpdateCoordinator, PWNReadingDraft
from .date import PWNReadingDate
from .number import PWNReadingDraftNumber
from .reading_request import (
    READING_REQUEST_SENSOR_DESCRIPTIONS,
    PWNReadingRequestBinarySensor,
    PWNReadingRequestSensor,
)
from .test_submit_workflow import _coordinator


def _entry() -> MagicMock:
    """Return an account entry with wholly fictitious identifiers."""
    entry = MagicMock()
    entry.unique_id = "fictional-account"
    entry.entry_id = "fictional-entry"
    return entry


def _acknowledge(coordinator: MagicMock) -> None:
    """Simulate only a successfully acknowledged final PWN API response."""
    PWNDataUpdateCoordinator.record_confirmed_meter_submission(
        coordinator,
        status_code=200,
        value=165,
        wheel_count=5,
        meter_number="00012345",
        reading_number="01234567890",
    )


class TestConfirmedRequestStatus(unittest.IsolatedAsyncioTestCase):
    """Ensure submitted IDs close their own entities without blocking new work."""

    async def test_successful_post_closes_cached_request_immediately(self) -> None:
        """The API may still say open after a confirmed final POST."""
        coordinator = _coordinator()
        entry = _entry()
        number = PWNReadingDraftNumber(coordinator, entry, "00012345")
        reading_date = PWNReadingDate(coordinator, entry, "00012345")
        prepare = PWNPrepareMeterReadingButton(coordinator, entry, "00012345")
        submit = PWNSubmitMeterReadingButton(coordinator, entry, "00012345")
        meter_status = PWNReadingRequestBinarySensor(
            coordinator, entry, "00012345"
        )
        overall_status = PWNReadingRequestBinarySensor(coordinator, entry)
        request_sensor = PWNReadingRequestSensor(
            coordinator,
            entry,
            "00012345",
            READING_REQUEST_SENSOR_DESCRIPTIONS[0],
        )

        self.assertTrue(number.available)
        self.assertTrue(reading_date.available)
        self.assertTrue(prepare.available)
        self.assertTrue(meter_status.is_on)
        await prepare.async_press()
        self.assertTrue(submit.available)

        await submit.async_press()

        coordinator.api.submit_meter_reading.assert_awaited_once()
        self.assertTrue(coordinator.data[DATA_READING_REQUESTS][0]["openReading"])
        for entity in (number, reading_date, prepare, submit, request_sensor):
            with self.subTest(entity=entity.unique_id):
                self.assertFalse(entity.available)
        self.assertIsNone(request_sensor.native_value)
        self.assertTrue(meter_status.available)
        self.assertFalse(meter_status.is_on)
        self.assertTrue(overall_status.available)
        self.assertFalse(overall_status.is_on)
        self.assertEqual(number.extra_state_attributes, {})

        with self.assertRaises(HomeAssistantError):
            await submit.async_press()
        coordinator.api.submit_meter_reading.assert_awaited_once()
        with patch.object(BaseCoordinatorEntity, "_handle_coordinator_update"):
            number._handle_coordinator_update()
        self.assertIsNone(number.native_value)
        self.assertNotIn("00012345", coordinator.draft_readings)
        coordinator.schedule_draft_save.assert_called_once_with()

    def test_new_reading_id_and_other_meter_remain_actionable(self) -> None:
        """Old confirmed and new open requests can coexist in a stale poll."""
        coordinator = _coordinator()
        _acknowledge(coordinator)
        old = coordinator.data[DATA_READING_REQUESTS][0]
        new = {**old, "readingNumber": "22222222222"}
        another = {
            **old,
            "meterNumber": "00054321",
            "readingNumber": "33333333333",
        }
        coordinator.data[DATA_READING_REQUESTS] = [old, new, another]
        coordinator.draft_readings["00012345"] = PWNReadingDraft(
            value=166,
            entered_at=datetime.now(timezone.utc),
            reading_number="22222222222",
            reading_date=datetime.now(timezone.utc).date(),
        )
        entry = _entry()
        number = PWNReadingDraftNumber(coordinator, entry, "00012345")
        status = PWNReadingRequestBinarySensor(coordinator, entry, "00012345")
        other_number = PWNReadingDraftNumber(coordinator, entry, "00054321")

        self.assertTrue(number.available)
        self.assertEqual(number._request_number, "22222222222")
        self.assertTrue(status.available)
        self.assertTrue(status.is_on)
        self.assertTrue(other_number.available)
        self.assertTrue(PWNReadingRequestBinarySensor(coordinator, entry).is_on)
        self.assertFalse(coordinator.was_reading_submitted("00012345", "22222222222"))
        self.assertIsNotNone(coordinator.current_submission_details("00012345"))
        self.assertTrue(coordinator.was_reading_submitted("00012345", "01234567890"))

    async def test_acknowledgement_restored_from_store_closes_request(self) -> None:
        """Recreated integration state cannot reopen a previously submitted ID."""
        original = _coordinator()
        _acknowledge(original)
        stored = PWNDataUpdateCoordinator._confirmed_storage_payload(original)
        restored = _coordinator()
        restored._confirmed_store.async_load = AsyncMock(return_value=stored)

        await PWNDataUpdateCoordinator.async_load_confirmed_readings(restored)

        self.assertTrue(restored.was_reading_submitted("00012345", "01234567890"))
        entry = _entry()
        self.assertFalse(PWNReadingDraftNumber(restored, entry, "00012345").available)
        self.assertFalse(PWNReadingDate(restored, entry, "00012345").available)
        self.assertFalse(
            PWNPrepareMeterReadingButton(restored, entry, "00012345").available
        )
        status = PWNReadingRequestBinarySensor(restored, entry, "00012345")
        self.assertTrue(status.available)
        self.assertFalse(status.is_on)
        self.assertFalse(PWNReadingRequestBinarySensor(restored, entry).is_on)

    async def test_refresh_finishing_after_acknowledgement_cannot_reopen(self) -> None:
        """An in-flight stale poll must not override per-request confirmation."""
        coordinator = _coordinator()
        started = asyncio.Event()
        release = asyncio.Event()

        async def _fetch_stale_data() -> dict[str, object]:
            """Wait until acknowledgement before returning the old open flag."""
            snapshot = dict(coordinator.data)
            started.set()
            await release.wait()
            return snapshot

        coordinator._fetch_all_data = AsyncMock(side_effect=_fetch_stale_data)
        task = asyncio.create_task(
            PWNDataUpdateCoordinator._async_update_data(coordinator)
        )
        await started.wait()
        _acknowledge(coordinator)
        release.set()
        coordinator.data = await task

        entry = _entry()
        self.assertFalse(
            PWNReadingDraftNumber(coordinator, entry, "00012345").available
        )
        status = PWNReadingRequestBinarySensor(coordinator, entry, "00012345")
        self.assertTrue(status.available)
        self.assertFalse(status.is_on)
        self.assertFalse(PWNReadingRequestBinarySensor(coordinator, entry).is_on)


if __name__ == "__main__":
    unittest.main()
