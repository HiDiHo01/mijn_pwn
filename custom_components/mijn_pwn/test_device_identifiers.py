"""Regression tests for PWN device registry identifiers."""

import unittest

from .const import (
    DOMAIN,
    SERVICE_NAME_CONSUMPTION,
    SERVICE_NAME_INVOICES,
    SERVICE_NAME_USER,
)
from .device_identifiers import get_device_identifiers


class TestDeviceIdentifiers(unittest.TestCase):
    """Validate the device registry's two-string identifier contract."""

    def test_invoices_retain_config_entry_identifier(self) -> None:
        """Invoice devices use the config entry ID without a service suffix."""
        self.assertEqual(
            get_device_identifiers("entry-123", SERVICE_NAME_INVOICES),
            {(DOMAIN, "entry-123")},
        )
        self.assertEqual(
            get_device_identifiers("entry-123", None),
            {(DOMAIN, "entry-123")},
        )

    def test_other_services_have_distinct_identifiers(self) -> None:
        """User and consumption devices must not share invoice identifiers."""
        self.assertEqual(
            get_device_identifiers("entry-123", SERVICE_NAME_USER),
            {(DOMAIN, "entry-123_User")},
        )
        self.assertEqual(
            get_device_identifiers("entry-123", SERVICE_NAME_CONSUMPTION),
            {(DOMAIN, "entry-123_Consumption")},
        )

    def test_identifiers_contain_exactly_two_strings(self) -> None:
        """Identifiers conform to the Home Assistant device registry type."""
        for service_name in (
            None,
            SERVICE_NAME_INVOICES,
            SERVICE_NAME_USER,
            SERVICE_NAME_CONSUMPTION,
        ):
            with self.subTest(service_name=service_name):
                identifiers = get_device_identifiers("entry-123", service_name)
                self.assertTrue(identifiers)
                for identifier in identifiers:
                    self.assertEqual(len(identifier), 2)
                    self.assertTrue(all(isinstance(part, str) for part in identifier))

    def test_rejects_empty_entry_id(self) -> None:
        """Invalid configuration entry IDs must not reach the device registry."""
        with self.assertRaisesRegex(ValueError, "config entry ID"):
            get_device_identifiers("", SERVICE_NAME_INVOICES)

    def test_rejects_empty_service_name(self) -> None:
        """Explicit empty service names are not valid identifiers."""
        with self.assertRaisesRegex(ValueError, "service name"):
            get_device_identifiers("entry-123", " ")
