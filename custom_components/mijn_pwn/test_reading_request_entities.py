"""Regression tests for reading-request entities and per-meter discovery."""

from datetime import datetime, timezone
import unittest
from unittest.mock import MagicMock

from .const import DATA_READING_REQUESTS, DATA_USER
from .coordinator import PWNDataUpdateCoordinator, PWNReadingDraft
from .reading_request import (
    READING_REQUEST_SENSOR_DESCRIPTIONS,
    PWNReadingRequestBinarySensor,
    PWNReadingRequestSensor,
    _get_request_for_meter,
    _parse_timestamp,
    _reading_address,
    _reading_value,
    setup_reading_request_binary_sensors,
    setup_reading_request_sensors,
)


def _reading_request(meter_number: str = "00123") -> dict[str, object]:
    """Create a fictional water-meter reading request."""
    return {
        "openReading": True,
        "readingNumber": "99999999991",
        "readingStartDate": "2026-10-25T00:00:00.0000000Z",
        "readingDate": "2026-11-21T00:00:00.0000000Z",
        "readingReason": 1,
        "amountOfWheels": 5,
        "meterNumber": meter_number,
        "address": {
            "street": "Example Street",
            "houseNumber": "1",
            "houseNumberAddition": "",
            "postalCode": "0000 AA",
            "city": "Example City",
            "country": "NL",
        },
        "contractStatus": 1,
    }


def _build_coordinator(
    reading_requests: list[dict[str, object]] | None,
) -> MagicMock:
    """Create a fake coordinator with validated reading-request data."""
    coordinator = MagicMock()
    coordinator.data = {DATA_READING_REQUESTS: reading_requests}
    coordinator.last_update_success = True
    coordinator._closed_reading_requests = {}
    coordinator.confirmed_readings = {}
    coordinator.was_reading_submitted.side_effect = (
        lambda meter_number, reading_number: (
            reading_number in coordinator.confirmed_readings.get(meter_number, {})
        )
    )
    coordinator.is_reading_request_closed.side_effect = (
        lambda meter_number, reading_number: (
            coordinator._closed_reading_requests.get(meter_number) == reading_number
        )
    )
    return coordinator


def _build_entry() -> MagicMock:
    """Create a fake Home Assistant config entry."""
    entry = MagicMock()
    entry.entry_id = "synthetic-entry"
    entry.unique_id = "synthetic-account"
    return entry


class TestReadingValues(unittest.TestCase):
    """Check typed sensor states and safe request selection."""

    def test_all_reading_fields(self) -> None:
        """Decode each supported field using its correct Home Assistant state type."""
        request = _reading_request()
        values = {
            "reading_number": "99999999991",
            "reading_start_date": datetime(2026, 10, 25, tzinfo=timezone.utc),
            "reading_date": datetime(2026, 11, 21, tzinfo=timezone.utc),
            "reading_reason": 1,
            "reading_wheel_count": 5,
            "reading_meter_number": "00123",
            "reading_address": "Example Street 1, 0000 AA Example City NL",
            "reading_contract_status": 1,
        }
        for description in READING_REQUEST_SENSOR_DESCRIPTIONS:
            with self.subTest(field=description.key):
                self.assertEqual(
                    _reading_value(request, description.key),
                    values[description.key],
                )

    def test_missing_or_invalid_timestamps(self) -> None:
        """Never return a naive or malformed timestamp to a timestamp entity."""
        for value in (None, "2026-11-21", "not-a-date", 42):
            with self.subTest(value=value):
                self.assertIsNone(_parse_timestamp(value))
        timestamp = _parse_timestamp("2026-11-21T00:00:00.0000000Z")
        self.assertIsNotNone(timestamp)
        self.assertIsNotNone(timestamp.tzinfo)

    def test_invalid_values_and_address(self) -> None:
        """Reject wrong types and excessively long address states."""
        request = _reading_request()
        request["readingReason"] = True
        self.assertIsNone(_reading_value(request, "reading_reason"))
        request["address"] = {"street": "X" * 300}
        self.assertIsNone(_reading_address(request))
        request["address"] = None
        self.assertIsNone(_reading_address(request))

    def test_no_request_and_ambiguous_requests(self) -> None:
        """Never select an arbitrary meter when the API returns duplicates."""
        request = _reading_request()
        other_request = _reading_request()
        other_request["readingNumber"] = "99999999992"
        self.assertIsNone(_get_request_for_meter({}, "00123"))
        self.assertIsNone(_get_request_for_meter({DATA_READING_REQUESTS: []}, "00123"))
        self.assertIsNone(
            _get_request_for_meter(
                {DATA_READING_REQUESTS: [request, other_request]}, "00123"
            )
        )
        other_request["openReading"] = False
        self.assertEqual(
            _get_request_for_meter(
                {DATA_READING_REQUESTS: [request, other_request]}, "00123"
            ),
            request,
        )


class TestReadingEntities(unittest.TestCase):
    """Verify entity state, availability, and stable device registration."""

    def test_meter_entities_follow_latest_coordinator_data(self) -> None:
        """Update each sensor from its own meter and preserve the unique ID."""
        coordinator = _build_coordinator([_reading_request()])
        entry = _build_entry()
        descriptions = {item.key: item for item in READING_REQUEST_SENSOR_DESCRIPTIONS}
        entity = PWNReadingRequestSensor(
            coordinator, entry, "00123", descriptions["reading_number"]
        )
        self.assertTrue(entity.available)
        self.assertEqual(entity.native_value, "99999999991")
        self.assertEqual(
            entity.unique_id,
            "synthetic-account_reading_meter_00123_reading_number",
        )
        self.assertEqual(entity.translation_placeholders, {"meter_number": "00123"})
        self.assertEqual(entity.device_info["translation_key"], "consumption")
        coordinator.data = {DATA_READING_REQUESTS: []}
        self.assertFalse(entity.available)
        self.assertIsNone(entity.native_value)

    def test_overall_and_meter_binary_sensors(self) -> None:
        """Distinguish an empty request list from a missing API response."""
        coordinator = _build_coordinator([_reading_request()])
        entry = _build_entry()
        overall = PWNReadingRequestBinarySensor(coordinator, entry)
        meter = PWNReadingRequestBinarySensor(coordinator, entry, "00123")
        self.assertTrue(overall.available)
        self.assertTrue(overall.is_on)
        self.assertTrue(meter.is_on)

        coordinator.data = {DATA_READING_REQUESTS: []}
        self.assertTrue(overall.available)
        self.assertFalse(overall.is_on)
        self.assertFalse(meter.available)
        self.assertIsNone(meter.is_on)

        coordinator.data = {DATA_READING_REQUESTS: None}
        self.assertFalse(overall.available)
        self.assertIsNone(overall.is_on)

    def test_reading_visible_without_account_but_submission_requires_identity(
        self,
    ) -> None:
        """Show independently fetched requests but refuse incomplete account writes."""
        request = _reading_request()
        request.pop("readingStartDate")
        request.pop("readingDate")
        coordinator = _build_coordinator([request])
        coordinator.data[DATA_USER] = None
        coordinator.draft_readings = {
            "00123": PWNReadingDraft(
                value=165,
                entered_at=datetime.now(timezone.utc),
                reading_number="99999999991",
                reading_date=datetime.now(timezone.utc).date(),
            )
        }
        for helper_name in (
            "_find_open_meter_request",
            "_valid_draft_for_request",
            "_extract_relation_number",
            "_valid_meter_address",
            "_reading_date_in_window",
        ):
            setattr(
                coordinator,
                helper_name,
                getattr(PWNDataUpdateCoordinator, helper_name),
            )

        overall = PWNReadingRequestBinarySensor(coordinator, _build_entry())

        self.assertTrue(overall.available)
        self.assertTrue(overall.is_on)
        self.assertIsNone(
            PWNDataUpdateCoordinator.current_submission_details(coordinator, "00123")
        )

        coordinator.data[DATA_USER] = {"relationNumber": "1234567"}
        valid = PWNDataUpdateCoordinator.current_submission_details(
            coordinator, "00123"
        )

        self.assertIsNotNone(valid)
        self.assertEqual(valid.relation_number, "1234567")

    def test_address_attributes_not_leaked_to_other_sensors(self) -> None:
        """Show address details only on the requested address entity."""
        coordinator = _build_coordinator([_reading_request()])
        entry = _build_entry()
        descriptions = {item.key: item for item in READING_REQUEST_SENSOR_DESCRIPTIONS}
        address = PWNReadingRequestSensor(
            coordinator, entry, "00123", descriptions["reading_address"]
        )
        reading_number = PWNReadingRequestSensor(
            coordinator, entry, "00123", descriptions["reading_number"]
        )
        self.assertEqual(address.extra_state_attributes["postalCode"], "0000 AA")
        self.assertEqual(reading_number.extra_state_attributes, {})

    def test_discovery_adds_new_meters_only_once(self) -> None:
        """Add sensors on first refresh and discover meters added later."""
        coordinator = _build_coordinator([_reading_request()])
        entry = _build_entry()
        add_entities = MagicMock()
        setup_reading_request_sensors(coordinator, entry, add_entities)
        self.assertEqual(add_entities.call_count, 1)
        self.assertEqual(
            len(add_entities.call_args.args[0]),
            len(READING_REQUEST_SENSOR_DESCRIPTIONS),
        )
        self.assertEqual(entry.async_on_unload.call_count, 1)
        callback = coordinator.async_add_listener.call_args.args[0]
        callback()
        self.assertEqual(add_entities.call_count, 1)

        coordinator.data = {
            DATA_READING_REQUESTS: [_reading_request(), _reading_request("00456")]
        }
        callback()
        self.assertEqual(add_entities.call_count, 2)
        self.assertEqual(
            len(add_entities.call_args.args[0]),
            len(READING_REQUEST_SENSOR_DESCRIPTIONS),
        )

    def test_binary_discovery_and_unload_listener(self) -> None:
        """Add overall and per-meter binary entities with unload cleanup."""
        coordinator = _build_coordinator([_reading_request()])
        entry = _build_entry()
        add_entities = MagicMock()
        setup_reading_request_binary_sensors(coordinator, entry, add_entities)
        self.assertEqual(add_entities.call_count, 2)
        self.assertEqual(len(add_entities.call_args_list[0].args[0]), 1)
        self.assertEqual(len(add_entities.call_args_list[1].args[0]), 1)
        self.assertEqual(entry.async_on_unload.call_count, 1)
