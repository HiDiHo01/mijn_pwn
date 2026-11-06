import unittest
from unittest.mock import MagicMock

from .binary_sensor import PWNBinarySensor
from .const import ATTRIBUTION, DATA_INVOICES


class TestPWNBinarySensor(unittest.TestCase):
    def setUp(self):
        """Set up the test environment."""
        self.coordinator = MagicMock()
        self.sensor = PWNBinarySensor(self.coordinator)

    def test_initial_state(self):
        """Test the initial state of the sensor."""
        self.assertEqual(self.sensor._attr_name, "Mijn PWN Binary Sensor")
        self.assertFalse(self.sensor.is_on)

    def test_is_on_no_data(self):
        """Test the sensor state when there is no data."""
        self.coordinator.data = {}
        self.assertFalse(self.sensor.is_on)

    def test_is_on_with_data(self):
        """Test the sensor state with invoice data."""
        self.coordinator.data = {DATA_INVOICES: {"numberOfOpenItems": 1}}
        self.assertTrue(self.sensor.is_on)

        self.coordinator.data = {DATA_INVOICES: {"numberOfOpenItems": 0}}
        self.assertFalse(self.sensor.is_on)

    def test_extra_state_attributes(self):
        """Test the extra state attributes."""
        self.coordinator.data = {DATA_INVOICES: {"numberOfOpenItems": 1}}
        expected_attributes = {
            "attribution": ATTRIBUTION,
            "invoices": {"numberOfOpenItems": 1},
        }
        self.assertEqual(self.sensor.extra_state_attributes, expected_attributes)


if __name__ == "__main__":
    unittest.main()
