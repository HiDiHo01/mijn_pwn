"""Build and migrate PWN device identifiers without changing device registry IDs."""

import logging

from homeassistant.helpers import device_registry

from .const import (
    DOMAIN,
    SERVICE_NAME_CONSUMPTION,
    SERVICE_NAME_INVOICES,
    SERVICE_NAME_USER,
)

_LOGGER = logging.getLogger(__name__)
_KNOWN_SERVICES = frozenset(
    (None, SERVICE_NAME_INVOICES, SERVICE_NAME_USER, SERVICE_NAME_CONSUMPTION)
)


def get_device_identifiers(
    entry_id: str,
    service_name: str | None,
) -> set[tuple[str, str]]:
    """Return the stable two-string identifier for a PWN service device."""
    if not entry_id or not entry_id.strip():
        raise ValueError("PWN device identifier requires a non-empty config entry ID")

    if service_name is not None and not service_name.strip():
        raise ValueError("PWN service name must be non-empty or None")

    identifier = (
        entry_id
        if service_name is None or service_name == SERVICE_NAME_INVOICES
        else f"{entry_id}_{service_name}"
    )
    return {(DOMAIN, identifier)}


def migrate_legacy_device_identifiers(
    registry: device_registry.DeviceRegistry,
    entry_id: str,
) -> int:
    """Convert legacy three-part identifiers while preserving registered device IDs.

    Only devices owned by this config entry and identifiers produced by older
    PWN releases are migrated. Unknown identifiers and collisions are left intact.
    This migration is idempotent and must run before forwarding entity platforms.
    """
    migrated = 0

    for device in device_registry.async_entries_for_config_entry(registry, entry_id):
        new_identifiers: set[tuple[str, str]] = set()
        targets: set[tuple[str, str]] = set()
        found_legacy = False
        unsupported = False

        for identifier in device.identifiers:
            if len(identifier) == 2:
                new_identifiers.add(identifier)
                continue

            if (
                len(identifier) != 3
                or identifier[0] != DOMAIN
                or identifier[1] != entry_id
                or identifier[2] not in _KNOWN_SERVICES
            ):
                unsupported = True
                break

            replacement = next(iter(get_device_identifiers(entry_id, identifier[2])))
            new_identifiers.add(replacement)
            targets.add(replacement)
            found_legacy = True

        if unsupported:
            _LOGGER.warning(
                "Skipping Mijn PWN device migration with unsupported identifiers"
            )
            continue

        if not found_legacy:
            continue

        if any(
            (existing := registry.async_get_device_by_identifier(target, entry_id))
            is not None
            and existing.id != device.id
            for target in targets
        ):
            _LOGGER.warning(
                "Skipping Mijn PWN device migration because the target is in use"
            )
            continue

        registry.async_update_device(device.id, new_identifiers=new_identifiers)
        migrated += 1

    if migrated:
        _LOGGER.info("Migrated %d legacy Mijn PWN devices", migrated)
    return migrated
