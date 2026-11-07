"""Two-step, explicitly confirmed PWN meter reading submission buttons."""

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity, UpdateFailed

from .api import PWNAPIError, PWNReadingNotPlausibleError
from .const import DOMAIN
from .coordinator import PWNDataUpdateCoordinator, PWNSubmissionDetails
from .reading_request import _reading_device_info, _register_meter_entities


class PWNReadingActionButton(CoordinatorEntity, ButtonEntity):
    """Base for manual per-meter actions; never send from a state change."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: PWNDataUpdateCoordinator,
        entry: ConfigEntry,
        meter_number: str,
        action: str,
    ) -> None:
        """Initialize a stable per-meter action."""
        super().__init__(coordinator)
        self._meter_number = meter_number
        self._attr_unique_id = (
            f"{entry.unique_id}_reading_meter_{meter_number}_{action}"
        )
        self._attr_translation_placeholders = {"meter_number": meter_number}
        self._attr_device_info = _reading_device_info(entry.entry_id)

    def _current_details(self) -> PWNSubmissionDetails | None:
        """Get the current draft snapshot, rejecting stale or missing requests."""
        return self.coordinator.current_submission_details(self._meter_number)

    @property
    def available(self) -> bool:
        """Permit actions only for a complete, not-yet-submitted draft."""
        details = self._current_details()
        confirmed = self.coordinator.last_confirmed_meter_reading
        return (
            super().available
            and details is not None
            and (
                confirmed is None or confirmed.reading_number != details.reading_number
            )
        )


class PWNPrepareMeterReadingButton(PWNReadingActionButton):
    """Check a draft against PWN without sending the meter reading."""

    _attr_translation_key = "prepare_meter_reading"
    _attr_icon = "mdi:clipboard-check-outline"

    def __init__(
        self,
        coordinator: PWNDataUpdateCoordinator,
        entry: ConfigEntry,
        meter_number: str,
    ) -> None:
        """Register the check-and-prepare action."""
        super().__init__(coordinator, entry, meter_number, "prepare_meter_reading")

    async def async_press(self) -> None:
        """Check plausibility and arm a confirmation that expires in two minutes."""
        if not self.available:
            raise HomeAssistantError(
                "Enter a meter reading for an open PWN request before checking"
            )
        details = self._current_details()
        if details is None:
            raise HomeAssistantError("The PWN reading request is no longer available")

        try:
            await self.coordinator.async_prepare_meter_reading(details)
        except PWNReadingNotPlausibleError:
            raise HomeAssistantError(
                "PWN rejected the reading as implausible; correct it before retrying"
            ) from None
        except PWNAPIError, UpdateFailed, ValueError:
            raise HomeAssistantError(
                "PWN could not approve this meter reading; nothing was submitted"
            ) from None


class PWNSubmitMeterReadingButton(PWNReadingActionButton):
    """Send only a validated draft after a second, explicit user action."""

    _attr_translation_key = "submit_meter_reading"
    _attr_icon = "mdi:send-check"

    def __init__(
        self,
        coordinator: PWNDataUpdateCoordinator,
        entry: ConfigEntry,
        meter_number: str,
    ) -> None:
        """Keep the existing submission button ID for compatibility."""
        super().__init__(coordinator, entry, meter_number, "submit_meter_reading")

    @property
    def available(self) -> bool:
        """Only enable the final button after a matching unexpired check."""
        details = self._current_details()
        return (
            super().available
            and details is not None
            and self.coordinator.is_prepared_submission(details)
        )

    async def async_press(self) -> None:
        """Transmit the unchanged, approved reading exactly once on confirmation."""
        if not self.available:
            raise HomeAssistantError(
                "First check the meter reading, then confirm within two minutes"
            )
        details = self._current_details()
        if details is None:
            raise HomeAssistantError("The PWN reading request has changed")

        try:
            await self.coordinator.async_confirm_meter_reading(details)
        except PWNAPIError, UpdateFailed, ValueError:
            raise HomeAssistantError(
                "PWN has not confirmed the reading; check your PWN account before retrying"
            ) from None


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create separate prepare and confirm buttons for each discovered meter."""
    coordinator: PWNDataUpdateCoordinator = hass.data[DOMAIN][config_entry.entry_id]

    def _make_buttons(meter_number: str) -> list[CoordinatorEntity]:
        """Create one of each button, with distinct stable unique IDs."""
        return [
            PWNPrepareMeterReadingButton(coordinator, config_entry, meter_number),
            PWNSubmitMeterReadingButton(coordinator, config_entry, meter_number),
        ]

    _register_meter_entities(
        coordinator, config_entry, async_add_entities, _make_buttons
    )
