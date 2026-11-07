"""Regression tests for PWN meter readings confirmed by the final server response."""

from datetime import datetime, timezone
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.helpers.update_coordinator import BaseCoordinatorEntity

from .confirmed_reading import (
    CONFIRMED_READING_DESCRIPTIONS,
    PWNConfirmedReadingSensor,
    _valid_restored_value,
)
from .coordinator import PWNDataUpdateCoordinator


def _make_coordinator() -> MagicMock:
    """Build a coordinator with no acknowledged PWN meter submission."""
    coordinator = MagicMock()
    coordinator.last_confirmed_meter_reading = None
    coordinator.confirmed_readings = {}
    coordinator.last_update_success = False
    return coordinator


def _make_sensor(coordinator: MagicMock, key: str) -> PWNConfirmedReadingSensor:
    """Create a sensor with a stable ID using fictional account details."""
    entry = MagicMock()
    entry.unique_id = "fictional-account"
    entry.entry_id = "fictional-entry"
    description = next(
        item for item in CONFIRMED_READING_DESCRIPTIONS if item.key == key
    )
    return PWNConfirmedReadingSensor(coordinator, entry, description)


class TestConfirmedReading(unittest.IsolatedAsyncioTestCase):
    """Prevent any unconfirmed draft or preliminary API result from being saved."""

    def test_sensors_start_unavailable_without_acknowledgement(self) -> None:
        """Do not substitute PWN consumption history for API submission proof."""
        coordinator = _make_coordinator()
        coordinator.data = {
            "consumption_history": {
                "history": [
                    {
                        "lastActualReading": True,
                        "meterReading": 12345,
                        "endDate": "2026-06-01",
                    }
                ]
            }
        }
        for description in CONFIRMED_READING_DESCRIPTIONS:
            sensor = _make_sensor(coordinator, description.key)
            with self.subTest(sensor=description.key):
                self.assertFalse(sensor.available)
                self.assertIsNone(sensor.native_value)
                self.assertEqual(sensor.extra_state_attributes, {})
        date_sensor = _make_sensor(coordinator, "last_submitted_meter_reading_date")
        self.assertEqual(date_sensor.device_class, SensorDeviceClass.TIMESTAMP)

    def test_record_only_final_submission_success(self) -> None:
        """Accept 200 or 204 only after explicit final-submission callback."""
        for status_code in (200, 204):
            with self.subTest(status_code=status_code):
                coordinator = _make_coordinator()
                reading_sensor = _make_sensor(
                    coordinator, "last_submitted_meter_reading"
                )
                date_sensor = _make_sensor(
                    coordinator, "last_submitted_meter_reading_date"
                )
                self.assertIsNone(reading_sensor.native_value)
                PWNDataUpdateCoordinator.record_confirmed_meter_submission(
                    coordinator,
                    status_code=status_code,
                    value=123,
                    wheel_count=5,
                    meter_number="00012345",
                    reading_number="01234567890",
                )
                self.assertTrue(reading_sensor.available)
                self.assertTrue(date_sensor.available)
                self.assertEqual(reading_sensor.native_value, 123)
                self.assertEqual(
                    reading_sensor.extra_state_attributes["formatted_reading"],
                    "00123",
                )
                self.assertTrue(reading_sensor.extra_state_attributes["pwn_confirmed"])
                self.assertIsInstance(date_sensor.native_value, datetime)
                self.assertEqual(date_sensor.native_value.tzinfo, timezone.utc)
                self.assertEqual(
                    reading_sensor.unique_id,
                    "fictional-account_last_submitted_meter_reading",
                )
                coordinator.async_update_listeners.assert_called_once_with()

    def test_reject_unconfirmed_status_and_invalid_reading(self) -> None:
        """Do not persist failed or unknown responses, even if the value is valid."""
        for status_code in (None, True, 0, 201, 202, 400, 401, 500):
            coordinator = _make_coordinator()
            with self.subTest(status_code=status_code):
                with self.assertRaisesRegex(ValueError, "final submission status"):
                    PWNDataUpdateCoordinator.record_confirmed_meter_submission(
                        coordinator,
                        status_code=status_code,
                        value=123,
                        wheel_count=5,
                        meter_number="00012345",
                        reading_number="01234567890",
                    )
                self.assertIsNone(coordinator.last_confirmed_meter_reading)
                coordinator.async_update_listeners.assert_not_called()

        for invalid_value, invalid_wheels, invalid_reading_number in (
            (-1, 5, "01234567890"),
            (100000, 5, "01234567890"),
            (True, 5, "01234567890"),
            (123, 0, "01234567890"),
            (123, 5, "123"),
        ):
            coordinator = _make_coordinator()
            with self.assertRaises(ValueError):
                PWNDataUpdateCoordinator.record_confirmed_meter_submission(
                    coordinator,
                    status_code=204,
                    value=invalid_value,
                    wheel_count=invalid_wheels,
                    meter_number="00012345",
                    reading_number=invalid_reading_number,
                )
            self.assertIsNone(coordinator.last_confirmed_meter_reading)

    def test_failed_retry_preserves_previous_confirmed_reading(self) -> None:
        """Keep the last confirmed value and timestamp when a later POST fails."""
        coordinator = _make_coordinator()
        reading_sensor = _make_sensor(coordinator, "last_submitted_meter_reading")
        date_sensor = _make_sensor(coordinator, "last_submitted_meter_reading_date")
        PWNDataUpdateCoordinator.record_confirmed_meter_submission(
            coordinator,
            status_code=204,
            value=165,
            wheel_count=5,
            meter_number="00012345",
            reading_number="01234567890",
        )
        previous = coordinator.last_confirmed_meter_reading
        previous_date = date_sensor.native_value
        coordinator.async_update_listeners.reset_mock()

        for status_code in (400, 409, 500):
            with self.subTest(status_code=status_code):
                with self.assertRaises(ValueError):
                    PWNDataUpdateCoordinator.record_confirmed_meter_submission(
                        coordinator,
                        status_code=status_code,
                        value=175,
                        wheel_count=5,
                        meter_number="00012345",
                        reading_number="01234567890",
                    )
                self.assertIs(coordinator.last_confirmed_meter_reading, previous)
                self.assertEqual(reading_sensor.native_value, 165)
                self.assertEqual(date_sensor.native_value, previous_date)

        coordinator.async_update_listeners.assert_not_called()

    def test_restored_value_validation(self) -> None:
        """Reject untrusted and timezone-naive values from restored states."""
        self.assertEqual(_valid_restored_value(123.0, False), 123)
        self.assertIsNone(_valid_restored_value(True, False))
        self.assertIsNone(_valid_restored_value(-1, False))
        self.assertIsNone(_valid_restored_value(12.5, False))
        self.assertIsNone(_valid_restored_value("123", False))
        self.assertIsNone(_valid_restored_value(datetime(2026, 11, 7, 9, 0), True))
        timestamp = datetime(2026, 11, 7, 9, 0, tzinfo=timezone.utc)
        self.assertEqual(_valid_restored_value(timestamp, True), timestamp)

    async def test_restore_only_prior_confirmed_submission(self) -> None:
        """Restore a confirmed value while excluding the superseded PWN history."""
        for key, value in (
            ("last_submitted_meter_reading", 123.0),
            (
                "last_submitted_meter_reading_date",
                datetime(2026, 11, 7, 9, 0, tzinfo=timezone.utc),
            ),
        ):
            coordinator = _make_coordinator()
            sensor = _make_sensor(coordinator, key)
            stored = MagicMock()
            stored.native_value = value
            previous_state = MagicMock()
            previous_state.attributes = {
                "source": "pwn_submission",
                "pwn_confirmed": True,
                "meter_number": "00012345",
                "reading_number": "01234567890",
                "formatted_reading": "00123",
                "confirmed_at": "2026-11-07T09:00:00+00:00",
            }
            with (
                patch.object(
                    BaseCoordinatorEntity,
                    "async_added_to_hass",
                    new_callable=AsyncMock,
                ),
                patch.object(
                    sensor,
                    "async_get_last_state",
                    new_callable=AsyncMock,
                    return_value=previous_state,
                ),
                patch.object(
                    sensor,
                    "async_get_last_sensor_data",
                    new_callable=AsyncMock,
                    return_value=stored,
                ),
            ):
                await sensor.async_added_to_hass()
            self.assertTrue(sensor.available)
            self.assertEqual(
                sensor.native_value, int(value) if key.endswith("reading") else value
            )
            self.assertTrue(sensor.extra_state_attributes["pwn_confirmed"])

    async def test_missing_confirmation_time_does_not_restore_reading(self) -> None:
        """Never present an incomplete restored status as a PWN acknowledgement."""
        coordinator = _make_coordinator()
        sensor = _make_sensor(coordinator, "last_submitted_meter_reading")
        previous_state = MagicMock()
        previous_state.attributes = {
            "source": "pwn_submission",
            "pwn_confirmed": True,
            "meter_number": "00012345",
            "reading_number": "01234567890",
            "formatted_reading": "00165",
        }
        stored = MagicMock()
        stored.native_value = 165.0
        with (
            patch.object(
                BaseCoordinatorEntity,
                "async_added_to_hass",
                new_callable=AsyncMock,
            ),
            patch.object(
                sensor,
                "async_get_last_state",
                new_callable=AsyncMock,
                return_value=previous_state,
            ),
            patch.object(
                sensor,
                "async_get_last_sensor_data",
                new_callable=AsyncMock,
                return_value=stored,
            ),
        ):
            await sensor.async_added_to_hass()

        self.assertIsNone(sensor.native_value)
        self.assertFalse(sensor.available)
        self.assertIsNone(coordinator.last_confirmed_meter_reading)

    async def test_restore_prevents_duplicate_submissions_after_restart(self) -> None:
        """Hydrate coordinator only from verified final submission metadata."""
        coordinator = _make_coordinator()
        sensor = _make_sensor(coordinator, "last_submitted_meter_reading")
        previous_state = MagicMock()
        previous_state.attributes = {
            "source": "pwn_submission",
            "pwn_confirmed": True,
            "meter_number": "00012345",
            "reading_number": "01234567890",
            "formatted_reading": "00165",
            "confirmed_at": "2026-11-07T09:01:00+00:00",
        }
        stored = MagicMock()
        stored.native_value = 165.0
        with (
            patch.object(
                BaseCoordinatorEntity,
                "async_added_to_hass",
                new_callable=AsyncMock,
            ),
            patch.object(
                sensor,
                "async_get_last_state",
                new_callable=AsyncMock,
                return_value=previous_state,
            ),
            patch.object(
                sensor,
                "async_get_last_sensor_data",
                new_callable=AsyncMock,
                return_value=stored,
            ),
        ):
            await sensor.async_added_to_hass()

        confirmed = coordinator.last_confirmed_meter_reading
        self.assertIsNotNone(confirmed)
        self.assertEqual(confirmed.value, 165)
        self.assertEqual(confirmed.reading_number, "01234567890")
        self.assertEqual(
            confirmed.confirmed_at,
            datetime(2026, 11, 7, 9, 1, tzinfo=timezone.utc),
        )
        coordinator.async_update_listeners.assert_called_once_with()

    async def test_never_restore_local_draft_or_unconfirmed_history(self) -> None:
        """Skip old sensor state lacking final submission confirmation."""
        for source, confirmed in (
            (None, None),
            ("home_assistant", False),
            ("pwn_submission", False),
        ):
            coordinator = _make_coordinator()
            sensor = _make_sensor(coordinator, "last_submitted_meter_reading")
            previous_state = MagicMock()
            previous_state.attributes = {
                "source": source,
                "pwn_confirmed": confirmed,
                "meter_number": "00012345",
                "reading_number": "01234567890",
                "formatted_reading": "00123",
            }
            stored = MagicMock()
            stored.native_value = 123
            with (
                patch.object(
                    BaseCoordinatorEntity,
                    "async_added_to_hass",
                    new_callable=AsyncMock,
                ),
                patch.object(
                    sensor,
                    "async_get_last_state",
                    new_callable=AsyncMock,
                    return_value=previous_state,
                ),
                patch.object(
                    sensor,
                    "async_get_last_sensor_data",
                    new_callable=AsyncMock,
                    return_value=stored,
                ),
            ):
                await sensor.async_added_to_hass()
            self.assertIsNone(sensor.native_value)
            self.assertFalse(sensor.available)
