"""Explicit user-operated PWN water meter submission buttons."""

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import PWNAPIError, PWNReadingNotPlausibleError
from .const import DATA_USER, DOMAIN
from .coordinator import PWNDataUpdateCoordinator
from .number import _get_open_request
from .reading_request import _reading_device_info, _register_meter_entities


def _relation_number(account_data: object) -> str | None:
    """Use only seven raw relation digits or a confirmed 000-prefixed identifier."""
    if not isinstance(account_data, dict):
        return None
    number = account_data.get("relationNumber")
    if not isinstance(number, str) or not number.isascii() or not number.isdecimal():
        return None
    if len(number) == 7:
        return number
    if len(number) == 10 and number.startswith("000"):
        return number[3:]
    return None


class PWNSubmitMeterReadingButton(CoordinatorEntity, ButtonEntity):
    """Send the last explicitly entered reading only when this button is pressed."""

    _attr_has_entity_name = True
    _attr_translation_key = "submit_meter_reading"
    _attr_icon = "mdi:send-check"

    def __init__(
        self,
        coordinator: PWNDataUpdateCoordinator,
        entry: ConfigEntry,
        meter_number: str,
    ) -> None:
        """Create a per-meter submission action without performing network calls."""
        super().__init__(coordinator)
        self._meter_number = meter_number
        self._attr_unique_id = (
            f"{entry.unique_id}_reading_meter_{meter_number}_submit_meter_reading"
        )
        self._attr_translation_placeholders = {"meter_number": meter_number}
        self._attr_device_info = _reading_device_info(entry.entry_id)

    @property
    def available(self) -> bool:
        """Enable only when a matching draft, request, and relation ID exist."""
        request = _get_open_request(self.coordinator, self._meter_number)
        draft = self.coordinator.draft_readings.get(self._meter_number)
        account_data = (
            self.coordinator.data.get(DATA_USER)
            if isinstance(self.coordinator.data, dict)
            else None
        )
        already_submitted = self.coordinator.last_confirmed_meter_reading
        return (
            super().available
            and request is not None
            and draft is not None
            and draft.reading_number == request.get("readingNumber")
            and isinstance(request.get("address"), dict)
            and _relation_number(account_data) is not None
            and (
                already_submitted is None
                or already_submitted.reading_number != draft.reading_number
            )
        )

    async def async_press(self) -> None:
        """Check plausibility and submit only after an explicit button press."""
        if not self.available:
            raise HomeAssistantError(
                "No complete, open PWN reading draft is available for submission"
            )
        request = _get_open_request(self.coordinator, self._meter_number)
        draft = self.coordinator.draft_readings.get(self._meter_number)
        account_data = self.coordinator.data.get(DATA_USER)
        relation_number = _relation_number(account_data)
        if request is None or draft is None or relation_number is None:
            raise HomeAssistantError("PWN meter-reading data is no longer available")

        address = request.get("address")
        if not isinstance(address, dict):
            raise HomeAssistantError("PWN meter address is unavailable")

        try:
            await self.coordinator.async_submit_meter_reading(
                reading_number=draft.reading_number,
                relation_number=relation_number,
                meter_number=self._meter_number,
                meter_reading=draft.value,
                wheel_count=request["amountOfWheels"],
                reading_date=dt_util.as_local(draft.entered_at).date(),
                address=address,
            )
        except PWNReadingNotPlausibleError:
            raise HomeAssistantError(
                "PWN rejected the reading as implausible; please check the reading and date"
            ) from None
        except PWNAPIError, UpdateFailed, ValueError:
            raise HomeAssistantError(
                "PWN has not confirmed the reading; check your PWN account before retrying"
            ) from None


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create a manual submission button for each discovered water meter."""
    coordinator: PWNDataUpdateCoordinator = hass.data[DOMAIN][config_entry.entry_id]

    def _make_button(meter_number: str) -> list[CoordinatorEntity]:
        """Build the per-meter manual submission action."""
        return [PWNSubmitMeterReadingButton(coordinator, config_entry, meter_number)]

    _register_meter_entities(
        coordinator, config_entry, async_add_entities, _make_button
    )
