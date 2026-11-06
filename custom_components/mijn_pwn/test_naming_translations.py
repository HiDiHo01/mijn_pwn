"""Regression tests for translated device and entity names in Mijn PWN."""

import json
from pathlib import Path
import unittest
from unittest.mock import MagicMock

from .binary_sensor import PWNBinarySensor, SENSOR_TYPES
from .const import (
    DOMAIN,
    SERVICE_NAME_CONSUMPTION,
    SERVICE_NAME_INVOICES,
    SERVICE_NAME_USER,
)
from .device_identifiers import get_device_identifiers, get_device_translation_key
from .sensor import SENSOR_DESCRIPTIONS
from .sensor_helper import PWNSensor

_COMPONENT_DIR = Path(__file__).resolve().parent
_DEVICE_KEYS = {"invoices", "user", "consumption"}


class TestNamingTranslations(unittest.TestCase):
    """Keep device identifiers stable and names translatable."""

    def test_supported_service_translation_keys(self) -> None:
        """Map supported services to lowercase keys without changing device IDs."""
        expected = {
            None: "invoices",
            SERVICE_NAME_INVOICES: "invoices",
            SERVICE_NAME_USER: "user",
            SERVICE_NAME_CONSUMPTION: "consumption",
        }
        for service_name, translation_key in expected.items():
            with self.subTest(service=service_name):
                self.assertEqual(
                    get_device_translation_key(service_name), translation_key
                )
        self.assertEqual(
            get_device_identifiers("entry-123", SERVICE_NAME_INVOICES),
            {(DOMAIN, "entry-123")},
        )
        self.assertEqual(
            get_device_identifiers("entry-123", SERVICE_NAME_USER),
            {(DOMAIN, "entry-123_User")},
        )
        self.assertEqual(
            get_device_identifiers("entry-123", SERVICE_NAME_CONSUMPTION),
            {(DOMAIN, "entry-123_Consumption")},
        )

    def test_reject_unsupported_service_translations(self) -> None:
        """Reject unknown service names instead of showing raw device keys."""
        for service_name in ("", "Unknown"):
            with self.subTest(service=service_name):
                with self.assertRaisesRegex(ValueError, "Unsupported PWN service"):
                    get_device_translation_key(service_name)

    def test_sensor_descriptions_use_translation_keys(self) -> None:
        """Every sensor uses a unique key instead of a hardcoded display name."""
        keys = [description.key for description in SENSOR_DESCRIPTIONS]
        self.assertEqual(len(keys), 20)
        self.assertEqual(len(keys), len(set(keys)))
        for description in SENSOR_DESCRIPTIONS:
            with self.subTest(sensor=description.key):
                self.assertEqual(description.translation_key, description.key)
                self.assertIsNone(description.name)

    def test_locales_have_matching_translation_keys(self) -> None:
        """Every shipped language covers all entity and service device names."""
        sensor_keys = {description.key for description in SENSOR_DESCRIPTIONS}
        binary_keys = {description.key for description in SENSOR_TYPES}
        english = json.loads((_COMPONENT_DIR / "strings.json").read_text("utf-8"))
        for locale in ("en", "nl", "es"):
            with self.subTest(locale=locale):
                catalog = json.loads(
                    (_COMPONENT_DIR / "translations" / f"{locale}.json").read_text(
                        "utf-8"
                    )
                )
                self.assertEqual(set(catalog["device"]), _DEVICE_KEYS)
                self.assertEqual(set(catalog["entity"]["sensor"]), sensor_keys)
                self.assertEqual(
                    set(catalog["entity"]["binary_sensor"]), binary_keys
                )
                for device in catalog["device"].values():
                    self.assertTrue(device["name"])
                for sensor in catalog["entity"]["sensor"].values():
                    self.assertTrue(sensor["name"])
                self.assertEqual(
                    set(catalog["config"]["step"]),
                    {"login", "user", "reconfigure"},
                )
                if locale == "en":
                    self.assertEqual(english, catalog)

    def test_platform_entities_use_translated_metadata(self) -> None:
        """Both platforms expose translation keys without overriding names."""
        coordinator = MagicMock()
        entry = MagicMock()
        entry.entry_id = "entry-123"
        entry.unique_id = "account-123"

        for service_name in (
            SERVICE_NAME_INVOICES,
            SERVICE_NAME_USER,
            SERVICE_NAME_CONSUMPTION,
        ):
            description = next(
                item
                for item in SENSOR_DESCRIPTIONS
                if item.service_name == service_name
            )
            with self.subTest(platform="sensor", service=service_name):
                entity = PWNSensor(coordinator, description, entry)
                self.assertTrue(entity.has_entity_name)
                self.assertNotIn("_attr_name", vars(entity))
                self.assertEqual(entity.translation_key, description.key)
                self.assertEqual(
                    entity._attr_device_info["translation_key"],
                    get_device_translation_key(service_name),
                )
                self.assertNotIn("name", entity._attr_device_info)
                self.assertEqual(
                    entity._attr_device_info["identifiers"],
                    get_device_identifiers(entry.entry_id, service_name),
                )
                self.assertEqual(
                    entity.unique_id, f"account-123_{description.key}"
                )

        binary_description = SENSOR_TYPES[0]
        binary_entity = PWNBinarySensor(coordinator, binary_description, entry)
        self.assertTrue(binary_entity.has_entity_name)
        self.assertNotIn("_attr_name", vars(binary_entity))
        self.assertEqual(binary_entity.translation_key, "open_invoices")
        self.assertEqual(binary_entity._attr_device_info["translation_key"], "invoices")
        self.assertNotIn("name", binary_entity._attr_device_info)
        self.assertEqual(
            binary_entity._attr_device_info["identifiers"],
            get_device_identifiers(entry.entry_id, SERVICE_NAME_INVOICES),
        )
        self.assertEqual(binary_entity.unique_id, "account-123_open_invoices")


if __name__ == "__main__":
    unittest.main()
