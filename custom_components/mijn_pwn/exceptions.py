"""Exceptions for the Frank Energie API."""


class PWNAPIAuthenticationError(Exception):
    """Exception raised for errors in the authentication with the PWN API."""
    pass


class FrankEnergieException(Exception):
    """Base exception."""


class AuthRequiredException(FrankEnergieException):
    """Authentication required for this request."""


class AuthException(FrankEnergieException):
    """Authentication/login failed."""


class NoSuitableSitesFoundError(Exception):
    """Request failed."""


class FrankEnergieError(Exception):
    """Base class for all FrankEnergie-related errors."""
    pass


class LoginError(FrankEnergieError):
    """Raised when login to FrankEnergie fails."""
    pass


class NetworkError(FrankEnergieError):
    """Raised for network-related errors in FrankEnergie."""
    pass


class RequestException(Exception):
    """Custom exception for request errors."""
    pass


class SmartTradingNotEnabledException(Exception):
    """Exception raised when smart trading is not enabled for the user."""
    pass


class ConnectionException(FrankEnergieError):
    """Raised for network-related errors in FrankEnergie."""
    pass
