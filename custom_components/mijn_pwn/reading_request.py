"""Home Assistant entities for open Mijn PWN meter-reading requests."""

from datetime import datetime
import logging
from typing import Callable

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    API_URL,
    ATTRIBUTION,
    DATA_READING_REQUESTS,
    MANUFACTURER,
    SERVICE_NAME_CONSUMPTION,
    VERSION,
)
from .coordinator import PWNDataUpdateCoordinator
from .device_identifiers import get_device_identifiers, get_device_translation_key

_LOGGER = logging.getLogger(__name__)

READING_REQUEST_SENSOR_DESCRIPTIONS: tuple[SensorEntityDescription, ...] = (
    SensorEntityDescription(
        key="reading_number",
        translation_key="reading_number",
        icon="mdi:identifier",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SensorEntityDescription(
        key="reading_start_date",
        translation_key="reading_start_date",
        icon="mdi:calendar-start",
        device_class=SensorDeviceClass.TIMESTAMP,
    ),
    SensorEntityDescription(
        key="reading_date",
        translation_key="reading_date",
        icon="mdi:calendar-clock",
        device_class=SensorDeviceClass.TIMESTAMP,
    ),
    SensorEntityDescription(
        key="reading_reason",
        translation_key="reading_reason",
        icon="mdi:information-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SensorEntityDescription(
        key="reading_wheel_count",
        translation_key="reading_wheel_count",
        icon="mdi:counter",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SensorEntityDescription(
        key="reading_meter_number",
        translation_key="reading_meter_number",
        icon="mdi:water-pump",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SensorEntityDescription(
        key="reading_address",
        translation_key="reading_address",
        icon="mdi:map-marker-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SensorEntityDescription(
        key="reading_contract_status",
        translation_key="reading_contract_status",
        icon="mdi:file-document-check-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
)


def _get_request_list(
    coordinator_data: dict[str, object] | None,
) -> list[dict[str, object]] | None:
    """Return validated reading-request entries or None when data is unavailable."""
    if not isinstance(coordinator_data, dict):
        return None
    requests = coordinator_data.get(DATA_READING_REQUESTS)
    if not isinstance(requests, list) or not all(
        isinstance(request, dict) for request in requests
    ):
        return None
    return requests


def _get_request_for_meter(
    coordinator_data: dict[str, object] | None, meter_number: str
) -> dict[str, object] | None:
    """Select an unambiguous request for a meter, preferring an open request."""
    requests = _get_request_list(coordinator_data)
    if requests is None:
        return None
    matches = [
        request for request in requests if request.get("meterNumber") == meter_number
    ]
    if len(matches) == 1:
        return matches[0]
    open_matches = [
        request for request in matches if request.get("openReading") is True
    ]
    if len(open_matches) == 1:
        return open_matches[0]
    # Never guess which request to display when the response is ambiguous.
    return None


def _is_closed_request(
    coordinator: PWNDataUpdateCoordinator,
    meter_number: str,
    request: dict[str, object],
) -> bool:
    """Identify a rejected request by its exact meter and opname number."""
    reading_number = request.get("readingNumber")
    return isinstance(reading_number, str) and coordinator.is_reading_request_closed(
        meter_number, reading_number
    )


def _parse_timestamp(value: object) -> datetime | None:
    """Parse an ISO 8601 timestamp only when it includes an explicit timezone."""
    if not isinstance(value, str):
        return None
    try:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return timestamp if timestamp.tzinfo is not None else None


def _reading_address(request: dict[str, object]) -> str | None:
    """Format the connection address without writing personal data to logs."""
    address = request.get("address")
    if not isinstance(address, dict):
        return None
    keys = ("street", "houseNumber", "houseNumberAddition")
    street = " ".join(
        value.strip()
        for key in keys
        if isinstance((value := address.get(key)), str) and value.strip()
    )
    location = " ".join(
        value.strip()
        for key in ("postalCode", "city", "country")
        if isinstance((value := address.get(key)), str) and value.strip()
    )
    result = ", ".join(part for part in (street, location) if part)
    return result if 0 < len(result) <= 255 else None


def _reading_value(
    request: dict[str, object], description_key: str
) -> str | int | datetime | None:
    """Return a correctly typed state for a supported reading-request field."""
    source_key = {
        "reading_number": "readingNumber",
        "reading_reason": "readingReason",
        "reading_wheel_count": "amountOfWheels",
        "reading_meter_number": "meterNumber",
        "reading_contract_status": "contractStatus",
    }.get(description_key)

    if description_key in ("reading_start_date", "reading_date"):
        key = (
            "readingStartDate"
            if description_key == "reading_start_date"
            else "readingDate"
        )
        return _parse_timestamp(request.get(key))
    if description_key == "reading_address":
        return _reading_address(request)
    if source_key is None:
        return None
    value = request.get(source_key)
    if description_key in (
        "reading_reason",
        "reading_wheel_count",
        "reading_contract_status",
    ):
        return value if type(value) is int else None
    return value if isinstance(value, str) and value.strip() else None


def _reading_device_info(entry_id: str) -> DeviceInfo:
    """Attach reading entities to the existing consumption service device."""
    return DeviceInfo(
        identifiers=get_device_identifiers(entry_id, SERVICE_NAME_CONSUMPTION),
        translation_key=get_device_translation_key(SERVICE_NAME_CONSUMPTION),
        manufacturer=MANUFACTURER,
        entry_type=DeviceEntryType.SERVICE,
        configuration_url=API_URL,
        model=SERVICE_NAME_CONSUMPTION,
        sw_version=VERSION,
    )


class PWNReadingRequestSensor(CoordinatorEntity, SensorEntity):
    """Show one reading-request field for a specific water meter."""

    _attr_has_entity_name = True
    _attr_attribution = ATTRIBUTION

    def __init__(
        self,
        coordinator: PWNDataUpdateCoordinator,
        entry: ConfigEntry,
        meter_number: str,
        description: SensorEntityDescription,
    ) -> None:
        """Initialize an entity using a stable meter identifier."""
        super().__init__(coordinator)
        self.entity_description = description
        self._meter_number = meter_number
        self._attr_unique_id = (
            f"{entry.unique_id}_reading_meter_{meter_number}_{description.key}"
        )
        self._attr_translation_placeholders = {"meter_number": meter_number}
        self._attr_device_info = _reading_device_info(entry.entry_id)

    @property
    def available(self) -> bool:
        """Hide a meter request after PWN has definitively rejected it."""
        request = _get_request_for_meter(self.coordinator.data, self._meter_number)
        return (
            super().available
            and request is not None
            and not _is_closed_request(self.coordinator, self._meter_number, request)
        )

    @property
    def native_value(self) -> str | int | datetime | None:
        """Return a supported state from the selected meter-reading request."""
        request = _get_request_for_meter(self.coordinator.data, self._meter_number)
        if request is None:
            return None
        return _reading_value(request, self.entity_description.key)

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        """Expose the individual address fields only on the address entity."""
        if self.entity_description.key != "reading_address":
            return {}
        request = _get_request_for_meter(self.coordinator.data, self._meter_number)
        address = request.get("address") if request is not None else None
        if not isinstance(address, dict):
            return {}
        return {
            key: value
            for key, value in address.items()
            if key
            in {
                "street",
                "houseNumber",
                "houseNumberAddition",
                "postalCode",
                "city",
                "country",
            }
            and isinstance(value, str)
        }


class PWNReadingRequestBinarySensor(CoordinatorEntity, BinarySensorEntity):
    """Show whether a single meter, or any meter, has an open reading request."""

    _attr_has_entity_name = True
    _attr_attribution = ATTRIBUTION
    _attr_icon = "mdi:clipboard-text-clock-outline"

    def __init__(
        self,
        coordinator: PWNDataUpdateCoordinator,
        entry: ConfigEntry,
        meter_number: str | None = None,
    ) -> None:
        """Initialize an overall or per-meter request-status entity."""
        super().__init__(coordinator)
        self._meter_number = meter_number
        if meter_number is None:
            self._attr_unique_id = f"{entry.unique_id}_reading_requests_open"
            self._attr_translation_key = "reading_requests_open"
        else:
            self._attr_unique_id = (
                f"{entry.unique_id}_reading_meter_{meter_number}_reading_open"
            )
            self._attr_translation_key = "reading_request_open"
            self._attr_translation_placeholders = {"meter_number": meter_number}
        self._attr_device_info = _reading_device_info(entry.entry_id)

    @property
    def available(self) -> bool:
        """Report unavailable when request data is missing or ambiguous."""
        if not super().available:
            return False
        if self._meter_number is None:
            return _get_request_list(self.coordinator.data) is not None
        request = _get_request_for_meter(self.coordinator.data, self._meter_number)
        return request is not None and not _is_closed_request(
            self.coordinator, self._meter_number, request
        )

    @property
    def is_on(self) -> bool | None:
        """Return the open-reading flag, without assuming a request exists."""
        if self._meter_number is None:
            requests = _get_request_list(self.coordinator.data)
            return (
                any(
                    request.get("openReading") is True
                    and isinstance(request.get("meterNumber"), str)
                    and not _is_closed_request(
                        self.coordinator, request["meterNumber"], request
                    )
                    for request in requests
                )
                if requests is not None
                else None
            )
        request = _get_request_for_meter(self.coordinator.data, self._meter_number)
        if request is None:
            return None
        if _is_closed_request(self.coordinator, self._meter_number, request):
            return None
        is_open = request.get("openReading")
        return is_open if isinstance(is_open, bool) else None


def _register_meter_entities(
    coordinator: PWNDataUpdateCoordinator,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
    factory: Callable[[str], list[CoordinatorEntity]],
) -> None:
    """Discover new meters after coordinator refreshes without duplicate entities."""
    known_meters: set[str] = set()

    @callback
    def _add_new_meters() -> None:
        """Create entities for meter numbers not previously discovered."""
        requests = _get_request_list(coordinator.data)
        if requests is None:
            return
        new_entities: list[CoordinatorEntity] = []
        for request in requests:
            meter_number = request.get("meterNumber")
            if (
                not isinstance(meter_number, str)
                or not meter_number.strip()
                or meter_number in known_meters
            ):
                continue
            known_meters.add(meter_number)
            new_entities.extend(factory(meter_number))
        if new_entities:
            async_add_entities(new_entities)

    _add_new_meters()
    entry.async_on_unload(coordinator.async_add_listener(_add_new_meters))


def setup_reading_request_sensors(
    coordinator: PWNDataUpdateCoordinator,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Add the translated reading-request sensors for all discovered meters."""

    def _create_entities(meter_number: str) -> list[CoordinatorEntity]:
        """Build one set of sensors for an identified water meter."""
        return [
            PWNReadingRequestSensor(coordinator, entry, meter_number, description)
            for description in READING_REQUEST_SENSOR_DESCRIPTIONS
        ]

    _register_meter_entities(coordinator, entry, async_add_entities, _create_entities)


def setup_reading_request_binary_sensors(
    coordinator: PWNDataUpdateCoordinator,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Add the overall and per-meter open-reading request binary sensors."""
    async_add_entities([PWNReadingRequestBinarySensor(coordinator, entry)])

    def _create_entities(meter_number: str) -> list[CoordinatorEntity]:
        """Build a status entity for a single water meter."""
        return [PWNReadingRequestBinarySensor(coordinator, entry, meter_number)]

    _register_meter_entities(coordinator, entry, async_add_entities, _create_entities)
