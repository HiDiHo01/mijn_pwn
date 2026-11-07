"""Editable draft meter readings for Mijn PWN, without account submission."""

from datetime import datetime, timezone
from math import isfinite

from homeassistant.components.number import NumberMode, RestoreNumber
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import ATTRIBUTION, DOMAIN
from .coordinator import PWNDataUpdateCoordinator
from .reading_request import (
    _get_request_for_meter,
    _reading_device_info,
    _register_meter_entities,
)

MAX_SUPPORTED_WHEELS = 9


def _wheel_count(request: dict[str, object]) -> int | None:
    """Return a supported integer count without accepting booleans."""
    value = request.get("amountOfWheels")
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 1 <= value <= MAX_SUPPORTED_WHEELS
    ):
        return None
    return value


def _get_open_request(
    coordinator: PWNDataUpdateCoordinator, meter_number: str
) -> dict[str, object] | None:
    """Return an open request with a usable number of black/white digits."""
    request = _get_request_for_meter(coordinator.data, meter_number)
    if not isinstance(request, dict) or request.get("openReading") is not True:
        return None
    return request if _wheel_count(request) is not None else None


def format_meter_reading(value: int, wheel_count: int) -> str:
    """Format every black/white digit, including zeroes at the beginning."""
    if (
        type(value) is not int
        or type(wheel_count) is not int
        or not 1 <= wheel_count <= MAX_SUPPORTED_WHEELS
        or not 0 <= value < 10**wheel_count
    ):
        raise ValueError("Meter reading must fit the configured number of digits")
    return f"{value:0{wheel_count}d}"


class PWNReadingDraftNumber(CoordinatorEntity, RestoreNumber):
    """Keep an editable meter-reading draft locally in Home Assistant."""

    _attr_has_entity_name = True
    _attr_translation_key = "meter_reading_draft"
    _attr_attribution = ATTRIBUTION
    _attr_icon = "mdi:counter"
    _attr_mode = NumberMode.BOX
    _attr_native_min_value = 0
    _attr_native_step = 1

    def __init__(
        self,
        coordinator: PWNDataUpdateCoordinator,
        entry: ConfigEntry,
        meter_number: str,
    ) -> None:
        """Initialize a per-meter input without sending data to PWN."""
        super().__init__(coordinator)
        self._meter_number = meter_number
        self._attr_unique_id = (
            f"{entry.unique_id}_reading_meter_{meter_number}_meter_reading_draft"
        )
        self._attr_translation_placeholders = {"meter_number": meter_number}
        self._attr_device_info = _reading_device_info(entry.entry_id)
        self._attr_native_value: float | None = None
        self._last_entered_at: datetime | None = None
        request = _get_open_request(coordinator, meter_number)
        self._request_number = request.get("readingNumber") if request else None

    @property
    def available(self) -> bool:
        """Allow input only when a valid, open meter-reading request exists."""
        return (
            super().available
            and _get_open_request(self.coordinator, self._meter_number) is not None
        )

    @property
    def native_max_value(self) -> float:
        """Limit drafts to the configured black/white wheel count."""
        request = _get_open_request(self.coordinator, self._meter_number)
        if request is None:
            return 0
        wheel_count = _wheel_count(request)
        return float(10**wheel_count - 1) if wheel_count is not None else 0

    @property
    def extra_state_attributes(self) -> dict[str, str]:
        """Show the padded reading without altering the underlying number."""
        request = _get_open_request(self.coordinator, self._meter_number)
        value = self._attr_native_value
        if not self.available or request is None or value is None:
            return {}
        wheel_count = _wheel_count(request)
        if wheel_count is None:
            return {}
        attributes = {
            "formatted_reading": format_meter_reading(int(value), wheel_count),
            "reading_number": self._request_number,
        }
        if self._last_entered_at is not None:
            attributes["entered_at"] = self._last_entered_at.isoformat()
        return attributes

    async def async_added_to_hass(self) -> None:
        """Restore a draft only for the same open meter-reading request."""
        await super().async_added_to_hass()
        previous_state = await self.async_get_last_state()
        restored = await self.async_get_last_number_data()
        request = _get_open_request(self.coordinator, self._meter_number)
        if (
            request is None
            or previous_state is None
            or restored is None
            or previous_state.attributes.get("reading_number")
            != request.get("readingNumber")
        ):
            return

        value = restored.native_value
        if (
            isinstance(value, bool)
            or not isinstance(value, (float, int))
            or not isfinite(value)
            or not 0 <= value <= self.native_max_value
            or not float(value).is_integer()
        ):
            return

        timestamp = previous_state.attributes.get("entered_at")
        if not isinstance(timestamp, str):
            return
        try:
            entered_at = datetime.fromisoformat(timestamp)
        except ValueError:
            return
        if entered_at.tzinfo is None or entered_at.utcoffset() is None:
            return

        self._attr_native_value = float(value)
        self._last_entered_at = entered_at

    async def async_set_native_value(self, value: float) -> None:
        """Store an integer draft locally, never submitting to the PWN API."""
        if not self.available:
            raise HomeAssistantError("No open PWN meter-reading request is available")
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not isfinite(value)
            or not 0 <= value <= self.native_max_value
            or not float(value).is_integer()
        ):
            raise HomeAssistantError(
                "Invalid meter reading: enter a whole number within the meter range"
            )
        self._attr_native_value = float(value)
        self._last_entered_at = datetime.now(timezone.utc)
        self.async_write_ha_state()

    @callback
    def _handle_coordinator_update(self) -> None:
        """Clear an old draft when a different reading request opens."""
        request = _get_open_request(self.coordinator, self._meter_number)
        if request is not None:
            reading_number = request.get("readingNumber")
            if reading_number != self._request_number:
                self._attr_native_value = None
                self._last_entered_at = None
                self._request_number = reading_number
            elif (
                self._attr_native_value is not None
                and self._attr_native_value > self.native_max_value
            ):
                self._attr_native_value = None
                self._last_entered_at = None
        super()._handle_coordinator_update()


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create a draft number entity for each discovered PWN water meter."""
    coordinator: PWNDataUpdateCoordinator = hass.data[DOMAIN][config_entry.entry_id]

    def _make_number(meter_number: str) -> list[CoordinatorEntity]:
        """Build the draft input for a discovered meter."""
        return [PWNReadingDraftNumber(coordinator, config_entry, meter_number)]

    _register_meter_entities(
        coordinator, config_entry, async_add_entities, _make_number
    )
