"""
Constants for PWN (Provinciaal Waterleidingbedrijf van Noord-Holland) Integration
"""
# const.py
from dataclasses import dataclass
from typing import Any, Final, Literal, TypedDict

from homeassistant.const import Platform

# Attribution
ATTRIBUTION: Final[str] = "Data provided by PWN"
"""Attribution text for data sources."""

# Domain and unique ID
DOMAIN: Final[str] = "mijn_pwn"
"""The domain for the PWN integration."""
UNIQUE_ID: Final[str] = "mijn_pwn"
"""Unique ID for the PWN integration."""

# Component details
COMPONENT_TITLE: Final[str] = "Mijn PWN"
"""Title for the PWN integration component."""
MANUFACTURER: Final[str] = "PWN"
"""Manufacturer name for devices related to this integration."""

# Service Names
SERVICE_NAME_INVOICES: Final[Literal["Invoices"]] = "Invoices"
"""Service name for retrieving invoices."""

SERVICE_NAME_USER: Final[Literal["User"]] = "User"
"""Service name for retrieving user data."""

SERVICE_NAME_CONSUMPTION: Final[Literal["Consumption"]] = "Consumption"
"""Service name for retrieving consumption data."""

ICON: Final[str] = "mdi:currency-eur"
"""Icon used for the PWN integration in the Home Assistant UI."""

# Integration version
VERSION: Final[str] = "2024.9.14"
"""Current version of the PWN integration."""


class Services(TypedDict):
    """Typed dictionary for service names."""
    INVOICES: Literal["Invoices"]
    USER: Literal["User"]
    CONSUMPTION: Literal["Consumption"]


SERVICES: Final[Services] = {
    "INVOICES": SERVICE_NAME_INVOICES,
    "USER": SERVICE_NAME_USER,
    "CONSUMPTION": SERVICE_NAME_CONSUMPTION
}
"""Dictionary mapping service keys to service names."""

# Data keys
DATA_CONSUMPTION: Final[str] = "consumption_history"
"""Data key for consumption history."""

DATA_INVOICES: Final[str] = "invoices"
"""Data key for invoices."""

DATA_USER: Final[str] = "user_data"
"""Data key for user data."""


class DataKeys(TypedDict):
    """Typed dictionary for data keys."""
    INVOICES: Literal["invoices"]
    USER: Literal["user_data"]
    CONSUMPTION: Literal["consumption_history"]


DATA_KEYS: Final[DataKeys] = {
    "INVOICES": "invoices",
    "USER": "user_data",
    "CONSUMPTION": "consumption_history"
}
"""Dictionary mapping data keys to data identifiers."""

# URL and timeout
API_URL: Final[str] = "https://mijn.pwn.nl"
"""Base URL for the PWN API."""
API_TIMEOUT: Final[int] = 10
"""Default timeout in seconds for API requests."""
NETWORK_CONFIG: Final[dict[str, Any]] = {
    "API_URL": "https://mijn.pwn.nl",
    "API_TIMEOUT": 10  # 10 seconds
}
"""Network configuration settings including API URL and timeout."""

API_ENDPOINTS: Final[dict[str, str]] = {
    "login": f"{API_URL}/api/auth/login",
    "contact_preferences": f"{API_URL}/api/Customer/contact-preferences",
    "settings": f"{API_URL}/assets/configs/mijn-pwn-nl/settings.json",
    "takeover": f"{API_URL}/api/Takeover?domain=2",
    "invoices_summary": f"{API_URL}/api/Invoices/summary",
    "relation_data": f"{API_URL}/api/Customer/relation-data",
    "payment_info": f"{API_URL}/api/PaymentInfo",
    "advance": f"{API_URL}/api/advance",
    "notifications": f"{API_URL}/api/Notifications",
    "consumption_history": f"{API_URL}/api/connection/consumption-history/dashboard"
}
"""Dictionary containing API endpoints for various PWN services."""

# Configuration Constants
CONFIG_CONSTANTS: Final[dict[str, str]] = {
    "COORDINATOR": "coordinator",
    "AUTH_TOKEN": "auth_token",
    "REFRESH_TOKEN": "refresh_token"
}
"""Dictionary mapping configuration constants to their keys."""

PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
]
"""List of platforms supported by this integration."""


@dataclass
class DeviceResponseEntry:
    """Data class representing a single response entry for a device.

    Attributes:
        invoices (dict | None): Dictionary containing invoice data or None.
        user_data (dict | None): Dictionary containing user data or None.
        consumption_history (dict | None): Dictionary containing consumption history or None.
    """
    invoices: dict | None
    user_data: dict | None
    consumption_history: dict | None
