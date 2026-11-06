"""Regression tests for optional Mijn PWN sensor descriptions."""

import unittest
from unittest.mock import MagicMock

from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.const import EntityCategory

from .sensor import SENSOR_DESCRIPTIONS, _should_add_sensor


class TestOptionalSensors(unittest.TestCase):
    """Protect metadata, optional API behavior, and stable entity keys."""

    def test_sensor_metadata(self) -> None:
        """Keep date and enum metadata compatible with Home Assistant."""
        descriptions = {
            description.key: description for description in SENSOR_DESCRIPTIONS
        }
        status = descriptions["water_consumption_status"]
        self.assertEqual(status.device_class, SensorDeviceClass.ENUM)
        self.assertEqual(status.options, ["no_reported_anomaly", "anomaly_reported"])
        last_reading = descriptions["last_meter_reading_date"]
        self.assertEqual(last_reading.device_class, SensorDeviceClass.DATE)
        self.assertEqual(last_reading.entity_category, EntityCategory.DIAGNOSTIC)

    def test_notification_entity_only_for_supported_array(self) -> None:
        """Do not create an entity if the notifications schema is unknown."""
        coordinator = MagicMock()
        descriptions = {
            description.key: description for description in SENSOR_DESCRIPTIONS
        }
        notification = descriptions["notification_count"]
        coordinator.data = {"notifications": {"unknown": []}}
        self.assertFalse(_should_add_sensor(notification, coordinator))
        coordinator.data = {"notifications": []}
        self.assertTrue(_should_add_sensor(notification, coordinator))
        self.assertEqual(notification.value_fn(coordinator.data), 0)

    def test_history_entities_only_with_supported_data(self) -> None:
        """Create diagnostic entities only when validated records are present."""
        coordinator = MagicMock()
        descriptions = {
            description.key: description for description in SENSOR_DESCRIPTIONS
        }
        date_sensor = descriptions["last_meter_reading_date"]
        status_sensor = descriptions["water_consumption_status"]
        coordinator.data = {"consumption_history": {"history": []}}
        self.assertFalse(_should_add_sensor(date_sensor, coordinator))
        self.assertFalse(_should_add_sensor(status_sensor, coordinator))
        coordinator.data = {
            "consumption_history": {
                "history": [
                    {
                        "lastActualReading": True,
                        "endDate": "2025-04-30",
                        "anomalyReason": None,
                    }
                ]
            }
        }
        self.assertTrue(_should_add_sensor(date_sensor, coordinator))
        self.assertTrue(_should_add_sensor(status_sensor, coordinator))


if __name__ == "__main__":
    unittest.main()
