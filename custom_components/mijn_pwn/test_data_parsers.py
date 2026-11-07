"""Deterministic tests for validated account and consumption indicators."""

from datetime import date
import unittest

from .data_parsers import (
    get_last_meter_reading_date,
    get_last_reported_meter_reading,
    get_notification_count,
    get_water_consumption_status,
)


class TestNotificationCount(unittest.TestCase):
    """Only a confirmed array representation is safe to count."""

    def test_empty_and_nonempty_arrays(self) -> None:
        """Accept arrays without examining their private contents."""
        self.assertEqual(get_notification_count([]), 0)
        self.assertEqual(get_notification_count([{"private": "synthetic"}, {}]), 2)

    def test_unknown_response_shapes_remain_unavailable(self) -> None:
        """Reject unsupported wrappers instead of inventing response fields."""
        for payload in (None, {}, {"messages": [1]}, "secret", 42, True):
            with self.subTest(payload_type=type(payload).__name__):
                self.assertIsNone(get_notification_count(payload))


class TestConsumptionIndicators(unittest.TestCase):
    """Derive states only from explicitly actual, dated meter records."""

    def test_latest_actual_record_not_list_order(self) -> None:
        """Select the most recent actual reading, ignoring estimates."""
        payload = {
            "history": [
                {
                    "lastActualReading": True,
                    "endDate": "2024-01-01T00:00:00.0000Z",
                    "anomalyReason": "synthetic-report",
                },
                {
                    "lastActualReading": False,
                    "endDate": "2026-12-01",
                    "anomalyReason": "estimate-only",
                },
                {
                    "lastActualReading": True,
                    "endDate": "2025-04-30T00:00:00.0000Z",
                    "anomalyReason": None,
                },
            ]
        }
        self.assertEqual(get_last_meter_reading_date(payload), date(2025, 4, 30))
        self.assertEqual(get_water_consumption_status(payload), "no_reported_anomaly")

    def test_last_reported_reading_uses_latest_actual_record(self) -> None:
        """Use the newest dated actual reading, not the first or an estimate."""
        payload = {
            "history": [
                {
                    "lastActualReading": True,
                    "endDate": "2024-01-01",
                    "meterReading": 125,
                },
                {
                    "lastActualReading": False,
                    "endDate": "2027-01-01",
                    "meterReading": 99999,
                },
                {
                    "lastActualReading": True,
                    "endDate": "2025-06-01",
                    "meterReading": 320,
                },
            ]
        }
        self.assertEqual(get_last_reported_meter_reading(payload), 320)
        self.assertEqual(get_last_meter_reading_date(payload), date(2025, 6, 1))

    def test_invalid_last_reported_reading_is_unknown(self) -> None:
        """Do not treat malformed or negative readings as submitted readings."""
        base = {"lastActualReading": True, "endDate": "2025-06-01"}
        for value in (None, True, -1, float("nan"), float("inf"), "private"):
            with self.subTest(value_type=type(value).__name__):
                payload = {"history": [{**base, "meterReading": value}]}
                self.assertIsNone(get_last_reported_meter_reading(payload))

    def test_reported_anomaly(self) -> None:
        """Expose an anomaly category without publishing the reason."""
        payload = {
            "history": [
                {
                    "lastActualReading": True,
                    "endDate": "2025-05-01",
                    "anomalyReason": "synthetic-private-description",
                }
            ]
        }
        self.assertEqual(get_water_consumption_status(payload), "anomaly_reported")
        self.assertNotIn(
            "synthetic-private-description", get_water_consumption_status(payload)
        )

    def test_absent_reason_is_unknown(self) -> None:
        """Do not equate missing response fields with healthy consumption."""
        payload = {"history": [{"lastActualReading": True, "endDate": "2025-05-01"}]}
        self.assertIsNone(get_water_consumption_status(payload))
        self.assertEqual(get_last_meter_reading_date(payload), date(2025, 5, 1))

    def test_malformed_and_unavailable_history(self) -> None:
        """Reject absent or malformed histories without raising errors."""
        candidates = (
            None,
            [],
            {},
            {"history": None},
            {"history": "invalid"},
            {"history": [None, {}, {"lastActualReading": True, "endDate": 5}]},
            {"history": [{"lastActualReading": True, "endDate": "bad-date"}]},
        )
        for payload in candidates:
            with self.subTest(payload=repr(payload)):
                self.assertIsNone(get_last_meter_reading_date(payload))
                self.assertIsNone(get_water_consumption_status(payload))


if __name__ == "__main__":
    unittest.main()
