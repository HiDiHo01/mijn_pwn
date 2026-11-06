"""Regression tests for Mijn PWN device identifiers and registry migrations."""

from dataclasses import dataclass
import unittest
from unittest.mock import patch

from homeassistant.helpers import device_registry

from .const import (
    DOMAIN,
    SERVICE_NAME_CONSUMPTION,
    SERVICE_NAME_INVOICES,
    SERVICE_NAME_USER,
)
from .device_identifiers import (
    get_device_identifiers,
    migrate_legacy_device_identifiers,
)


@dataclass
class _RegistryDevice:
    """Store a synthetic device, including possibly malformed legacy identifiers."""

    id: str
    config_entry_id: str
    identifiers: set[tuple[object, ...]]


class _FakeDeviceRegistry:
    """Implement the registry operations used by the migration."""

    def __init__(self, devices: list[_RegistryDevice]) -> None:
        """Keep deterministic device records for isolation tests."""
        self.devices = devices
        self.updated_ids: list[str] = []

    def entries_for(self, config_entry_id: str) -> list[_RegistryDevice]:
        """Return devices belonging to one config entry."""
        return [
            device
            for device in self.devices
            if device.config_entry_id == config_entry_id
        ]

    def async_get_device_by_identifier(
        self, identifier: tuple[str, str], config_entry_id: str
    ) -> _RegistryDevice | None:
        """Find an existing device by a two-string identifier and its owner."""
        return next(
            (
                device
                for device in self.entries_for(config_entry_id)
                if identifier in device.identifiers
            ),
            None,
        )

    def async_update_device(
        self, device_id: str, *, new_identifiers: set[tuple[str, str]]
    ) -> None:
        """Simulate an in-place registry update without changing device IDs."""
        device = next(device for device in self.devices if device.id == device_id)
        device.identifiers = set(new_identifiers)
        self.updated_ids.append(device_id)


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
        """Every supported service must return a pair of strings."""
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
        """Reject empty or whitespace-only config entry IDs."""
        for value in ("", "  "):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "config entry ID"):
                    get_device_identifiers(value, SERVICE_NAME_INVOICES)

    def test_rejects_empty_service_name(self) -> None:
        """Reject explicitly blank service names."""
        with self.assertRaisesRegex(ValueError, "service name"):
            get_device_identifiers("entry-123", " ")

    def _migrate(self, registry: _FakeDeviceRegistry) -> int:
        """Call the real migration using a scoped registry fixture."""
        with patch.object(
            device_registry,
            "async_entries_for_config_entry",
            side_effect=lambda _registry, entry: registry.entries_for(entry),
        ):
            return migrate_legacy_device_identifiers(registry, "entry-123")

    def test_migrates_all_legacy_services_in_place(self) -> None:
        """Restore legacy device identifiers from saved three-item tuples."""
        devices = [
            _RegistryDevice("invoice-id", "entry-123", {(DOMAIN, "entry-123", None)}),
            _RegistryDevice(
                "user-id", "entry-123", {(DOMAIN, "entry-123", SERVICE_NAME_USER)}
            ),
            _RegistryDevice(
                "consumption-id",
                "entry-123",
                {(DOMAIN, "entry-123", SERVICE_NAME_CONSUMPTION)},
            ),
        ]
        registry = _FakeDeviceRegistry(devices)

        self.assertEqual(self._migrate(registry), 3)
        self.assertEqual(
            registry.updated_ids, ["invoice-id", "user-id", "consumption-id"]
        )
        self.assertEqual(devices[0].identifiers, {(DOMAIN, "entry-123")})
        self.assertEqual(devices[1].identifiers, {(DOMAIN, "entry-123_User")})
        self.assertEqual(devices[2].identifiers, {(DOMAIN, "entry-123_Consumption")})

        # Repeated startup must not create or update devices again.
        self.assertEqual(self._migrate(registry), 0)
        self.assertEqual(len(registry.updated_ids), 3)

    def test_preserves_other_identifiers_and_entries(self) -> None:
        """Keep unrelated identifiers and devices owned by a different entry."""
        own = _RegistryDevice(
            "own-id",
            "entry-123",
            {(DOMAIN, "entry-123", None), ("other_domain", "stable")},
        )
        foreign = _RegistryDevice(
            "foreign-id", "entry-456", {(DOMAIN, "entry-456", None)}
        )
        registry = _FakeDeviceRegistry([own, foreign])

        self.assertEqual(self._migrate(registry), 1)
        self.assertEqual(
            own.identifiers, {(DOMAIN, "entry-123"), ("other_domain", "stable")}
        )
        self.assertEqual(foreign.identifiers, {(DOMAIN, "entry-456", None)})
        self.assertEqual(registry.updated_ids, ["own-id"])

    def test_skips_collision_without_orphaning_device(self) -> None:
        """Never overwrite another device already using the target identifier."""
        old = _RegistryDevice("old-id", "entry-123", {(DOMAIN, "entry-123", None)})
        current = _RegistryDevice("current-id", "entry-123", {(DOMAIN, "entry-123")})
        registry = _FakeDeviceRegistry([old, current])

        self.assertEqual(self._migrate(registry), 0)
        self.assertEqual(old.identifiers, {(DOMAIN, "entry-123", None)})
        self.assertEqual(registry.updated_ids, [])

    def test_skips_unrecognized_identifier_shape(self) -> None:
        """Do not rewrite unsupported legacy or foreign identifier formats."""
        legacy = _RegistryDevice(
            "legacy-id", "entry-123", {(DOMAIN, "entry-123", "Unknown")}
        )
        registry = _FakeDeviceRegistry([legacy])

        self.assertEqual(self._migrate(registry), 0)
        self.assertEqual(legacy.identifiers, {(DOMAIN, "entry-123", "Unknown")})
        self.assertEqual(registry.updated_ids, [])


if __name__ == "__main__":
    unittest.main()
