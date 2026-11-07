"""
Binary Sensor platform for Mijn PWN.
"""

# binary_sensor.py
from dataclasses import dataclass, field
import logging
from typing import Callable, Union
import uuid

from homeassistant.components.binary_sensor import (
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import StateType
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    API_URL,
    ATTRIBUTION,
    DATA_INVOICES,
    DOMAIN,
    MANUFACTURER,
    SERVICE_NAME_INVOICES,
    VERSION,
)
from .coordinator import PWNDataUpdateCoordinator
from .device_identifiers import get_device_identifiers, get_device_translation_key
from .reading_request import setup_reading_request_binary_sensors

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Mijn PWN binary sensor based on a config entry."""
    _LOGGER.debug(
        "Setting up Mijn PWN binary sensor for entry: %s", config_entry.entry_id
    )

    # Ensure the config entry has a unique ID
    if config_entry.unique_id is None:
        unique_id = str(uuid.uuid4())
        hass.config_entries.async_update_entry(config_entry, unique_id=unique_id)
    else:
        unique_id = config_entry.unique_id

    coordinator: PWNDataUpdateCoordinator = hass.data[DOMAIN][config_entry.entry_id]
    async_add_entities(
        [
            PWNBinarySensor(coordinator, description, config_entry)
            for description in SENSOR_TYPES
            if not description.authenticated or coordinator.api.is_authenticated
        ],
        True,
    )
    setup_reading_request_binary_sensors(coordinator, config_entry, async_add_entities)


@dataclass(frozen=True, slots=True)
class PWNEntityDescription(BinarySensorEntityDescription):
    """Representation of a Sensor."""

    key: str
    translation_key: str | None = None
    has_entity_name: bool = True
    authenticated: bool = False
    service_name: Union[str, None] = SERVICE_NAME_INVOICES
    value_fn: Callable[[dict], StateType] | None = None
    attr_fn: Callable[[dict], dict[str, Union[StateType, list]]] = field(
        default_factory=lambda: {}  # type: ignore
    )


SENSOR_TYPES: tuple[PWNEntityDescription, ...] = (
    PWNEntityDescription(
        key="open_invoices",
        translation_key="open_invoices",
        icon="mdi:file-document-alert-outline",
        value_fn=lambda data: (
            data.get(DATA_INVOICES, {}).get("numberOfOpenItems", 0) > 0
            if data
            else False
        ),
    ),
)


class PWNBinarySensor(CoordinatorEntity, BinarySensorEntity):
    """Representation of a Mijn PWN binary sensor."""

    def __init__(
        self,
        coordinator: PWNDataUpdateCoordinator,
        description: PWNEntityDescription,
        entry: ConfigEntry,
    ):
        """Initialize the binary sensor."""
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{entry.unique_id}_{description.key}"

        device_info_identifiers: set[tuple[str, str]] = get_device_identifiers(
            entry.entry_id, description.service_name
        )

        self._attr_device_info = DeviceInfo(
            identifiers=device_info_identifiers,
            translation_key=get_device_translation_key(description.service_name),
            manufacturer=MANUFACTURER,
            entry_type=DeviceEntryType.SERVICE,
            configuration_url=API_URL,
            model=description.service_name,
            sw_version=VERSION,
        )

        _LOGGER.debug("PWNBinarySensor initialized with coordinator: %s", coordinator)

    def _open_invoice_count(self) -> int | None:
        """Return a verified invoice count, never treating missing data as zero."""
        data = self.coordinator.data
        if not isinstance(data, dict):
            return None
        invoices_data = data.get(DATA_INVOICES)
        if not isinstance(invoices_data, dict):
            return None
        summary = invoices_data.get("invoicesSummary")
        count = (
            summary.get("numberOfOpenItems")
            if isinstance(summary, dict)
            else invoices_data.get("numberOfOpenItems")
        )
        return count if type(count) is int and count >= 0 else None

    @property
    def available(self) -> bool:
        """Report unavailable when the invoice endpoint has no valid count."""
        return super().available and self._open_invoice_count() is not None

    @property
    def is_on(self) -> bool | None:
        """Return the actual open-invoice status without false negatives."""
        count = self._open_invoice_count()
        return count > 0 if count is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, str | dict | None]:
        """Return the state attributes."""
        data = self.coordinator.data or {}

        attributes: dict[str, str | dict | None] = {
            "attribution": ATTRIBUTION,
            "invoices": data.get(DATA_INVOICES),
            "last_update_success": str(self.coordinator.last_update_success),
            "last_exception": str(self.coordinator.last_exception)
            if self.coordinator.last_exception
            else None,
        }

        _LOGGER.debug("Extra state attributes set")
        return attributes
