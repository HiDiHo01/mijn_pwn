"""Tests for local, non-submitting PWN water meter reading drafts."""

import math
import unittest
from unittest.mock import MagicMock, patch

from homeassistant.components.number import NumberMode
from homeassistant.exceptions import HomeAssistantError

from .const import DATA_READING_REQUESTS, DOMAIN
from .number import PWNReadingDraftNumber, async_setup_entry, format_meter_reading


def _reading_request(
    reading_number: str = "12345678901",
    wheel_count: int = 5,
    meter_number: str = "00012345",
) -> dict[str, object]:
    """Create a fictional PWN reading request without personal information."""
    return {
        "openReading": True,
        "readingNumber": reading_number,
        "amountOfWheels": wheel_count,
        "meterNumber": meter_number,
    }


def _make_number(
    reading_requests: list[dict[str, object]] | None = None,
) -> tuple[PWNReadingDraftNumber, MagicMock]:
    """Build an editable number entity using a synthetic coordinator and entry."""
    coordinator = MagicMock()
    coordinator.data = {
        DATA_READING_REQUESTS: (
            [_reading_request()] if reading_requests is None else reading_requests
        )
    }
    coordinator.last_update_success = True
    entry = MagicMock()
    entry.unique_id = "synthetic-account"
    entry.entry_id = "synthetic-entry"
    return PWNReadingDraftNumber(coordinator, entry, "00012345"), coordinator


class TestReadingDraftNumber(unittest.IsolatedAsyncioTestCase):
    """Verify drafts are stored locally, with exact zero-padded display."""

    def test_fixed_width_formatter(self) -> None:
        """Preserve black/white wheel leading zeroes on display."""
        self.assertEqual(format_meter_reading(0, 5), "00000")
        self.assertEqual(format_meter_reading(123, 5), "00123")
        self.assertEqual(format_meter_reading(99999, 5), "99999")

        for value, wheel_count in (
            (-1, 5),
            (100000, 5),
            (1, 0),
            (1, 10),
            (True, 5),
            (1, True),
        ):
            with self.subTest(value=value, wheel_count=wheel_count):
                with self.assertRaises(ValueError):
                    format_meter_reading(value, wheel_count)

    async def test_number_platform_discovers_new_meters(self) -> None:
        """Add a translated input for each meter without duplicating entities."""
        entity, coordinator = _make_number()
        hass = MagicMock()
        config_entry = MagicMock()
        config_entry.unique_id = "synthetic-account"
        config_entry.entry_id = "synthetic-entry"
        hass.data = {DOMAIN: {config_entry.entry_id: coordinator}}
        added: list[PWNReadingDraftNumber] = []

        def _add_entities(entities: list[PWNReadingDraftNumber]) -> None:
            """Collect entities added by the number platform."""
            added.extend(entities)

        await async_setup_entry(hass, config_entry, _add_entities)
        self.assertEqual(len(added), 1)
        self.assertEqual(added[0].unique_id, entity.unique_id)

        callback = coordinator.async_add_listener.call_args.args[0]
        coordinator.data = {
            DATA_READING_REQUESTS: [
                _reading_request(),
                _reading_request(meter_number="00054321", reading_number="01234567890"),
            ]
        }
        callback()
        self.assertEqual(len(added), 2)
        self.assertEqual(
            added[1].unique_id,
            "synthetic-account_reading_meter_00054321_meter_reading_draft",
        )
        callback()
        self.assertEqual(len(added), 2)

    async def test_local_number_input_does_not_send_post(self) -> None:
        """Store a draft without calling the PWN client or any HTTP request."""
        entity, coordinator = _make_number()
        self.assertTrue(entity.available)
        self.assertIsNone(entity.native_value)
        self.assertEqual(entity.native_max_value, 99999)
        self.assertEqual(entity.native_step, 1)
        self.assertEqual(entity.mode, NumberMode.BOX)
        self.assertEqual(
            entity.unique_id,
            "synthetic-account_reading_meter_00012345_meter_reading_draft",
        )
        self.assertEqual(entity.device_info["translation_key"], "consumption")
        self.assertEqual(entity.translation_placeholders, {"meter_number": "00012345"})
        with patch.object(entity, "async_write_ha_state") as write_state:
            await entity.async_set_native_value(123)
            write_state.assert_called_once()

        self.assertEqual(entity.native_value, 123)
        self.assertEqual(entity.extra_state_attributes, {"formatted_reading": "00123"})
        coordinator.api.assert_not_called()

    async def test_refuse_invalid_values_and_closed_requests(self) -> None:
        """Reject fractional, nonfinite, and out-of-range values without writes."""
        entity, coordinator = _make_number()
        values: tuple[object, ...] = (-1, 100000, 12.5, math.nan, math.inf, True)
        with patch.object(entity, "async_write_ha_state") as write_state:
            for value in values:
                with self.subTest(value=value):
                    with self.assertRaises(HomeAssistantError):
                        await entity.async_set_native_value(value)
            write_state.assert_not_called()
        self.assertIsNone(entity.native_value)
        coordinator.data = {DATA_READING_REQUESTS: []}
        self.assertFalse(entity.available)
        self.assertEqual(entity.extra_state_attributes, {})
        with self.assertRaises(HomeAssistantError):
            await entity.async_set_native_value(123)

    async def test_draft_clears_for_next_reading_request(self) -> None:
        """Never carry a previous reading into a new PWN request."""
        entity, coordinator = _make_number()
        with patch.object(entity, "async_write_ha_state"):
            await entity.async_set_native_value(123)
            coordinator.data = {
                DATA_READING_REQUESTS: [_reading_request(reading_number="01234567890")]
            }
            entity._handle_coordinator_update()
        self.assertIsNone(entity.native_value)

    async def test_wheel_count_and_open_status_update(self) -> None:
        """Limit the range dynamically and mark unsupported meters unavailable."""
        entity, coordinator = _make_number()
        with patch.object(entity, "async_write_ha_state"):
            await entity.async_set_native_value(99999)
            coordinator.data = {
                DATA_READING_REQUESTS: [_reading_request(wheel_count=4)]
            }
            entity._handle_coordinator_update()
        self.assertTrue(entity.available)
        self.assertEqual(entity.native_max_value, 9999)
        self.assertIsNone(entity.native_value)

        coordinator.data = {DATA_READING_REQUESTS: [_reading_request(wheel_count=10)]}
        self.assertFalse(entity.available)
        coordinator.data = {
            DATA_READING_REQUESTS: [{**_reading_request(), "openReading": False}]
        }
        self.assertFalse(entity.available)
