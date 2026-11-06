"""Regression tests for optional Mijn PWN sensor descriptions."""

import unittest
from unittest.mock import MagicMock

from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.const import EntityCategory

from .binary_sensor import SENSOR_TYPES, PWNBinarySensor
from .sensor import SENSOR_DESCRIPTIONS, _should_add_sensor
from .sensor_helper import PWNSensor


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


    def test_contextual_icons_cover_all_entities(self) -> None:
        """Use explicit MDI icons rather than the generic currency fallback."""
        sensor_icons = {
            description.key: description.icon for description in SENSOR_DESCRIPTIONS
        }
        binary_icons = {
            description.key: description.icon for description in SENSOR_TYPES
        }
        self.assertEqual(len(sensor_icons), 23)
        self.assertEqual(binary_icons, {"open_invoices": "mdi:file-document-alert-outline"})
        for key, icon in (sensor_icons | binary_icons).items():
            with self.subTest(entity=key):
                self.assertIsInstance(icon, str)
                self.assertRegex(icon, r"^mdi:[a-z0-9]+(?:-[a-z0-9]+)*$")

        expected_icons = {
            "total_open_amount": "mdi:cash-clock",
            "total_costs": "mdi:cash-multiple",
            "expected_costs_current_year": "mdi:chart-line",
            "contract_account_number": "mdi:identifier",
            "relation_number": "mdi:card-account-details-outline",
            "birth_place": "mdi:map-marker",
            "date_of_birth": "mdi:cake-variant",
            "correspondence_address": "mdi:map-marker-outline",
            "latest_meter_reading": "mdi:gauge",
            "total_consumption": "mdi:water",
            "consumption_last_year": "mdi:water-circle",
            "water_consumption_status": "mdi:water-outline",
        }
        for key, icon in expected_icons.items():
            with self.subTest(entity=key):
                self.assertEqual(sensor_icons[key], icon)

    def test_entity_icons_follow_descriptions(self) -> None:
        """Ensure entity rendering uses the new icons without changing IDs."""
        coordinator = MagicMock()
        entry = MagicMock()
        entry.entry_id = "synthetic-entry"
        entry.unique_id = "synthetic-account"

        for description in SENSOR_DESCRIPTIONS:
            with self.subTest(sensor=description.key):
                entity = PWNSensor(coordinator, description, entry)
                self.assertEqual(entity.icon, description.icon)
                self.assertEqual(
                    entity.unique_id, f"synthetic-account_{description.key}"
                )

        for description in SENSOR_TYPES:
            with self.subTest(binary_sensor=description.key):
                entity = PWNBinarySensor(coordinator, description, entry)
                self.assertEqual(entity.icon, description.icon)
                self.assertEqual(
                    entity.unique_id, f"synthetic-account_{description.key}"
                )


if __name__ == "__main__":
    unittest.main()
