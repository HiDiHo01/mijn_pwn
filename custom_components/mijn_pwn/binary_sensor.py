"""
Binary Sensor platform for Mijn PWN.
"""
# binary_sensor.py
import logging
import uuid
from dataclasses import dataclass, field
from typing import Callable, Optional, Union

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import StateType
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    API_URL,
    ATTRIBUTION,
    COMPONENT_TITLE,
    DATA_INVOICES,
    DOMAIN,
    MANUFACTURER,
    SERVICE_NAME_INVOICES,
    VERSION,
)
from .coordinator import PWNDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback
) -> None:
    """Set up Mijn PWN binary sensor based on a config entry."""
    _LOGGER.debug("Setting up Mijn PWN binary sensor for entry: %s",
                  config_entry.entry_id)

    # Ensure the config entry has a unique ID
    if config_entry.unique_id is None:
        unique_id = str(uuid.uuid4())
        hass.config_entries.async_update_entry(
            config_entry, unique_id=unique_id)
    else:
        unique_id = config_entry.unique_id

    coordinator: PWNDataUpdateCoordinator = hass.data[DOMAIN][config_entry.entry_id]
    async_add_entities(
        [
            PWNBinarySensor(coordinator, description, config_entry)
            for description in SENSOR_TYPES
            if not description.authenticated
            or coordinator.api.auth.is_authenticated
        ],
        True,
    )


@dataclass
class PWNEntityDescription:
    """Representation of a Sensor."""
    key: str
    name: str
    device_class: Optional[str] = None
    state_class: Optional[str] = None
    native_unit_of_measurement: Optional[str] = None
    suggested_display_precision: Optional[int] = None
    authenticated: bool = False
    service_name: Union[str, None] = SERVICE_NAME_INVOICES
    value_fn: Optional[Callable[[dict], StateType]] = None
    attr_fn: Callable[[dict], dict[str, Union[StateType, list]]] = field(
        default_factory=lambda: {}  # type: ignore
    )
    entity_registry_enabled_default: bool = True
    entity_registry_visible_default: bool = True
    translation_key: Optional[str] = None
    icon: Optional[str] = None
    entity_category: Optional[str] = None
    force_update: bool = False

    def __post_init__(self):
        if self.value_fn is None:
            self.value_fn = lambda data: STATE_UNKNOWN
        if self.attr_fn is None:
            self.attr_fn = lambda data: {}

    @property
    def has_entity_name(self) -> bool:
        """Return if the entity has a name."""
        return bool(self.name)

    @property
    def unit_of_measurement(self) -> Optional[str]:
        """Return the unit of measurement."""
        return self.native_unit_of_measurement


SENSOR_TYPES: tuple[PWNEntityDescription, ...] = (
    PWNEntityDescription(
        key="open_invoices",
        name="Open invoices",
        translation_key="open_invoices",
        icon="mdi:alert",
        native_unit_of_measurement=None,
        value_fn=lambda data: data.get(
            DATA_INVOICES, {}).get('numberOfOpenItems', 0) > 0
        if data else None
    ),
)


class PWNBinarySensor(CoordinatorEntity, BinarySensorEntity):
    """Representation of a Mijn PWN binary sensor."""

    def __init__(self,
                 coordinator: PWNDataUpdateCoordinator,
                 description: PWNEntityDescription,
                 entry: ConfigEntry):
        """Initialize the binary sensor."""
        super().__init__(coordinator)
        self.entity_description: PWNEntityDescription = description
        self._attr_name = description.name
        self._attr_unique_id = f"{entry.unique_id}_{description.key}"
        # self._attr_unique_id = f"{entry.unique_id}_{description.key}_{str(uuid.uuid4())}"
        self._attr_is_on = self.is_on

        device_info_identifiers: set[tuple[str, str, Optional[str]]] = (
            {(DOMAIN, entry.entry_id, None)}
            if description.service_name is SERVICE_NAME_INVOICES
            else {(DOMAIN, entry.entry_id, description.service_name)}
        )

        self._attr_device_info = DeviceInfo(
            identifiers=device_info_identifiers,
            name=f"{COMPONENT_TITLE} - {description.service_name}",
            translation_key=f"{COMPONENT_TITLE} - {description.service_name}",
            manufacturer=MANUFACTURER,
            entry_type=DeviceEntryType.SERVICE,
            configuration_url=API_URL,
            model=description.service_name,
            sw_version=VERSION,
        )

        # self._update_state()

        _LOGGER.debug(
            "PWNBinarySensor initialized with coordinator: %s", coordinator)

    def _update_state(self):
        """Update the state of the sensor."""
        self._attr_is_on = self.entity_description.value_fn(
            self.coordinator.data)

    @property
    def name(self) -> str:
        """Return the name of the sensor."""
        return self._attr_name

    @property
    def is_on(self) -> bool:
        """Return the state of the sensor."""
        invoices_data = self.coordinator.data.get(DATA_INVOICES, {})
        if invoices_data:
            return invoices_data.get('numberOfOpenItems', 0) > 0
        _LOGGER.warning("No invoice data available for sensor: %s", self.name)
        return False

    @property
    def extra_state_attributes(self) -> dict[str, Union[str, dict]]:
        """Return the state attributes."""
        attributes = {
            "attribution": ATTRIBUTION,
            "invoices": self.coordinator.data.get(DATA_INVOICES),
        }
        # _LOGGER.debug("Extra state attributes: %s", attributes)
        _LOGGER.debug("Extra state attributes set")
        return attributes

    async def async_added_to_hass(self) -> None:
        """When entity is added to hass."""
        _LOGGER.debug("Entity %s added to hass", self._attr_name)
        await super().async_added_to_hass()

        # Ensure the coordinator is not None and has the async_add_listener method
        if self.coordinator and hasattr(self.coordinator, 'async_add_listener'):
            self.async_on_remove(
                self.coordinator.async_add_listener(self._update_state)
            )
        else:
            _LOGGER.error(
                "Coordinator is None or does not have async_add_listener method.")

    async def async_update(self) -> None:
        """Update the entity."""
        # await self.coordinator.async_request_refresh()
