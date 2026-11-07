"""Editable water meter observation dates for explicit PWN submissions."""

from dataclasses import replace
from datetime import date

from homeassistant.components.date import DateEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import PWNDataUpdateCoordinator
from .number import _get_open_request
from .reading_request import _reading_device_info, _register_meter_entities


class PWNReadingDate(CoordinatorEntity, DateEntity):
    """Let the user select the actual date the water meter was observed."""

    _attr_has_entity_name = True
    _attr_translation_key = "meter_reading_date_input"
    _attr_icon = "mdi:calendar-edit"

    def __init__(
        self,
        coordinator: PWNDataUpdateCoordinator,
        entry: ConfigEntry,
        meter_number: str,
    ) -> None:
        """Create a per-meter editable date linked to the local draft."""
        super().__init__(coordinator)
        self._meter_number = meter_number
        self._attr_unique_id = (
            f"{entry.unique_id}_reading_meter_{meter_number}_meter_reading_date_input"
        )
        self._attr_translation_placeholders = {"meter_number": meter_number}
        self._attr_device_info = _reading_device_info(entry.entry_id)
        draft = coordinator.draft_readings.get(meter_number)
        self._attr_native_value = draft.reading_date if draft is not None else None

    @property
    def available(self) -> bool:
        """Require a valid open meter request and an entered concept value."""
        return (
            super().available
            and _get_open_request(self.coordinator, self._meter_number) is not None
            and self._meter_number in self.coordinator.draft_readings
        )

    async def async_set_value(self, value: date) -> None:
        """Persist a selected date in the draft without submitting to PWN."""
        request = _get_open_request(self.coordinator, self._meter_number)
        draft = self.coordinator.draft_readings.get(self._meter_number)
        if (
            request is None
            or draft is None
            or draft.reading_number != request.get("readingNumber")
        ):
            raise HomeAssistantError("Enter a valid PWN meter reading first")
        if not self.coordinator._reading_date_in_window(value, request):
            raise HomeAssistantError(
                "Reading date must be within the PWN request period and not in the future"
            )

        self.coordinator.invalidate_prepared_submission(self._meter_number)
        self.coordinator.draft_readings[self._meter_number] = replace(
            draft, reading_date=value
        )
        self._attr_native_value = value
        self.coordinator.async_update_listeners()

    @callback
    def _handle_coordinator_update(self) -> None:
        """Reflect newly entered, restored or cleared draft dates."""
        draft = self.coordinator.draft_readings.get(self._meter_number)
        request = _get_open_request(self.coordinator, self._meter_number)
        self._attr_native_value = (
            draft.reading_date
            if draft is not None
            and request is not None
            and draft.reading_number == request.get("readingNumber")
            else None
        )
        super()._handle_coordinator_update()


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Add one separate actual meter reading date selector per water meter."""
    coordinator: PWNDataUpdateCoordinator = hass.data[DOMAIN][config_entry.entry_id]

    def _make_date(meter_number: str) -> list[CoordinatorEntity]:
        """Build the date selector for a discovered water meter."""
        return [PWNReadingDate(coordinator, config_entry, meter_number)]

    _register_meter_entities(coordinator, config_entry, async_add_entities, _make_date)
