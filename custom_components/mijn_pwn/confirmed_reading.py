"""Restore PWN meter submissions only after final API acknowledgement."""

from collections.abc import Mapping
from datetime import datetime
from math import isfinite

from homeassistant.components.sensor import (
    RestoreSensor,
    SensorDeviceClass,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfVolume
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import PWNConfirmedMeterReading, PWNDataUpdateCoordinator
from .number import format_meter_reading
from .reading_request import _reading_device_info

CONFIRMED_READING_DESCRIPTIONS: tuple[SensorEntityDescription, ...] = (
    SensorEntityDescription(
        key="last_submitted_meter_reading",
        translation_key="last_submitted_meter_reading",
        icon="mdi:counter",
        device_class=SensorDeviceClass.WATER,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=UnitOfVolume.CUBIC_METERS,
        suggested_display_precision=0,
    ),
    SensorEntityDescription(
        key="last_submitted_meter_reading_date",
        translation_key="last_submitted_meter_reading_date",
        icon="mdi:calendar-check",
        device_class=SensorDeviceClass.TIMESTAMP,
    ),
)


def _valid_restored_value(value: object, is_date: bool) -> int | datetime | None:
    """Validate a restored submission value without assuming it was genuine."""
    if is_date:
        if (
            isinstance(value, datetime)
            and value.tzinfo is not None
            and value.utcoffset() is not None
        ):
            return value
        return None
    if (
        type(value) in (int, float)
        and isfinite(value)
        and value >= 0
        and float(value).is_integer()
    ):
        return int(value)
    return None


def _restored_confirmation_ids(
    attributes: Mapping[str, object],
) -> tuple[str, str, str] | None:
    """Validate persisted identifiers and the formatted water meter reading."""
    reading_number = attributes.get("reading_number")
    meter_number = attributes.get("meter_number")
    formatted_reading = attributes.get("formatted_reading")
    if (
        not isinstance(reading_number, str)
        or len(reading_number) != 11
        or not reading_number.isascii()
        or not reading_number.isdecimal()
        or not isinstance(meter_number, str)
        or not 0 < len(meter_number) <= 64
        or not isinstance(formatted_reading, str)
        or not 1 <= len(formatted_reading) <= 9
        or not formatted_reading.isascii()
        or not formatted_reading.isdecimal()
    ):
        return None
    return reading_number, meter_number, formatted_reading


def _restored_confirmation_timestamp(value: object) -> datetime | None:
    """Parse a timezone-aware acknowledgement timestamp from saved state."""
    if not isinstance(value, str):
        return None
    try:
        timestamp = datetime.fromisoformat(value)
    except ValueError:
        return None
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        return None
    return timestamp


class PWNConfirmedReadingSensor(CoordinatorEntity, RestoreSensor):
    """Keep the most recently acknowledged PWN submission across restarts."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: PWNDataUpdateCoordinator,
        entry: ConfigEntry,
        description: SensorEntityDescription,
    ) -> None:
        """Initialize a per-account confirmed submission sensor."""
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{entry.unique_id}_{description.key}"
        self._attr_device_info = _reading_device_info(entry.entry_id)
        self._restored_value: int | datetime | None = None
        self._restored_attributes: dict[str, str] = {}

    @property
    def available(self) -> bool:
        """Retain confirmed readings even when the remote API is unavailable."""
        return (
            self.coordinator.last_confirmed_meter_reading is not None
            or self._restored_value is not None
        )

    @property
    def native_value(self) -> int | datetime | None:
        """Use a genuine final-submission acknowledgement, never PWN history."""
        reading = self.coordinator.last_confirmed_meter_reading
        if reading is not None:
            if self.entity_description.key == "last_submitted_meter_reading":
                return reading.value
            return reading.confirmed_at
        return self._restored_value

    @property
    def extra_state_attributes(self) -> dict[str, str | bool]:
        """Expose provenance and preserve the original black-wheel digits."""
        reading = self.coordinator.last_confirmed_meter_reading
        if reading is not None:
            return {
                "source": "pwn_submission",
                "pwn_confirmed": True,
                "meter_number": reading.meter_number,
                "reading_number": reading.reading_number,
                "formatted_reading": format_meter_reading(
                    reading.value, reading.wheel_count
                ),
                "confirmed_at": reading.confirmed_at.isoformat(),
            }
        if self._restored_value is None:
            return {}
        return {
            "source": "pwn_submission",
            "pwn_confirmed": True,
            **self._restored_attributes,
        }

    async def async_added_to_hass(self) -> None:
        """Restore only previously acknowledged PWN meter reading states."""
        await super().async_added_to_hass()
        if self.coordinator.last_confirmed_meter_reading is not None:
            return

        previous_state = await self.async_get_last_state()
        restored = await self.async_get_last_sensor_data()
        if (
            previous_state is None
            or restored is None
            or previous_state.attributes.get("source") != "pwn_submission"
            or previous_state.attributes.get("pwn_confirmed") is not True
        ):
            return

        is_date = self.entity_description.key == "last_submitted_meter_reading_date"
        restored_value = _valid_restored_value(restored.native_value, is_date)
        ids = _restored_confirmation_ids(previous_state.attributes)
        if restored_value is None or ids is None:
            return

        reading_number, meter_number, formatted_reading = ids
        if not is_date:
            if int(formatted_reading) != restored_value:
                return
            confirmed_at = _restored_confirmation_timestamp(
                previous_state.attributes.get("confirmed_at")
            )
            if confirmed_at is None:
                return
            reading = PWNConfirmedMeterReading(
                value=restored_value,
                wheel_count=len(formatted_reading),
                confirmed_at=confirmed_at,
                meter_number=meter_number,
                reading_number=reading_number,
            )
            self.coordinator.confirmed_readings.setdefault(meter_number, {})[
                reading_number
            ] = reading
            self.coordinator.last_confirmed_meter_reading = reading
            self.coordinator.async_update_listeners()

        self._restored_value = restored_value
        self._restored_attributes = {
            "reading_number": reading_number,
            "meter_number": meter_number,
            "formatted_reading": formatted_reading,
        }


def setup_confirmed_reading_sensors(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Register sensors without fetching or submitting any meter readings."""
    coordinator: PWNDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            PWNConfirmedReadingSensor(coordinator, entry, description)
            for description in CONFIRMED_READING_DESCRIPTIONS
        ]
    )
