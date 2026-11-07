"""Regression tests for the future Mijn PWN moving-address endpoint."""

import unittest

from .const import API_ENDPOINTS, API_URL, DATA_ENDPOINTS


class TestMoveHouseEndpoint(unittest.TestCase):
    """Keep the unverified MoveHouse endpoint registered without invoking it."""

    def test_validate_address_endpoint(self) -> None:
        """Preserve the original URL casing and the shared endpoint mapping."""
        expected_url = f"{API_URL}/api/MoveHouse/validate-address"
        self.assertEqual(API_ENDPOINTS["move_house_validate_address"], expected_url)
        self.assertEqual(DATA_ENDPOINTS["move_house_validate_address"], expected_url)
