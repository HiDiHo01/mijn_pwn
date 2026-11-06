"""Generate Home Assistant-compatible identifiers for PWN service devices."""

from .const import DOMAIN, SERVICE_NAME_INVOICES


def get_device_identifiers(
    entry_id: str,
    service_name: str | None,
) -> set[tuple[str, str]]:
    """Return a stable (domain, identifier) pair for a PWN service device."""
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
