"""Handels the authentication for Mijn PWN"""
# auth.py
# Mijn PWN API uses Azure services
import logging

import requests

from .const import DATA_URL, URL_TIMEOUT

_LOGGER = logging.getLogger(__name__)


class PWNAuth:
    """Handles the authentication for Mijn PWN using Azure services"""

    def __init__(self, username, password):
        self.username = username
        self.password = password
        self.access_token = None
        self.refresh_token = None
        self.api_login_url = f"{DATA_URL}auth/login"

    def login(self):
        """Login logic"""

        payload = {
            'user': self.username,
            'password': self.password
        }

        try:
            response = requests.post(
                self.api_login_url, json=payload, timeout=URL_TIMEOUT)
            if response.status_code == 200:
                tokens = response.json()
                self.access_token = tokens.get('accessToken')
                self.refresh_token = tokens.get('refreshToken')
                _LOGGER.info("Login successful")
                return True
            else:
                _LOGGER.error("Failed to login. Status code: %s",
                              response.status_code)
                return False

        except requests.exceptions.RequestException as e:
            _LOGGER.error("Error occurred during login: %s", e)
            return False

    def refresh_tokens(self):
        """Token refresh logic"""
        if not self.refresh_token:
            _LOGGER.error("No refresh token available. Need to login first.")
            return False

        payload = {
            'user': self.username,
            'password': self.password,
            'refresh_token': self.refresh_token
        }

        try:
            response = requests.post(
                self.api_login_url, json=payload, timeout=URL_TIMEOUT)
            if response.status_code == 200:
                tokens = response.json()
                self.access_token = tokens.get('accessToken')
                self.refresh_token = tokens.get('refreshToken')
                _LOGGER.info("Token refresh successful")
                return True
            else:
                _LOGGER.error(
                    "Failed to refresh tokens. Status code: %s", response.status_code)
                return False
        except requests.exceptions.RequestException as e:
            _LOGGER.error("Error occurred during token refresh: %s", e)
            return False

    def logout(self):
        """ Logout logic """
        self.access_token = None
        self.refresh_token = None
        _LOGGER.info("Logged out successfully")
