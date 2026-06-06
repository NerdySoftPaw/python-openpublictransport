"""Exceptions for python-openpublictransport."""


class AuthenticationError(Exception):
    """Raised when API authentication fails (HTTP 401 or 403).

    Signals that the configured API key is invalid or expired.
    Callers should prompt the user to re-enter credentials rather than retrying.
    """
