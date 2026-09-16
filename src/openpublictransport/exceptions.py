"""Exceptions for python-openpublictransport.

Every failure raised by this library derives from :class:`OpenPublicTransportError`.
Callers can therefore catch the whole family with a single ``except`` clause, or
discriminate on the concrete type to tell a server-side outage apart from a
network problem or an empty result.

Empty results are *not* errors: ``search_stops()`` returns ``[]`` and
``fetch_departures()`` returns ``None`` only when the provider genuinely had no
data to give.
"""

from typing import Optional


class OpenPublicTransportError(Exception):
    """Base class for every error raised by this library."""


class ApiError(OpenPublicTransportError):
    """Raised when a provider API answers with a non-200 HTTP status.

    The HTTP status is available as :attr:`status` so callers can report it to
    the user instead of silently treating the failure as "no results".
    :attr:`body` holds a truncated copy of the error response, which is often
    the only place a provider explains what it disliked.
    """

    def __init__(
        self,
        provider: str,
        status: int,
        message: Optional[str] = None,
        body: Optional[str] = None,
    ) -> None:
        self.provider = provider
        self.status = status
        self.body = body
        if message is None:
            message = f"{provider}: API returned HTTP {status}"
            if body:
                message = f"{message} — {body}"
        super().__init__(message)


class ApiConnectionError(OpenPublicTransportError):
    """Raised when the provider API cannot be reached (DNS, TCP or TLS failure)."""


class ApiTimeoutError(ApiConnectionError):
    """Raised when the provider API does not answer within the timeout."""


class ApiResponseError(OpenPublicTransportError):
    """Raised when a 200 response carries a payload that cannot be used.

    Covers undecodable JSON/XML as well as a well-formed body whose shape does
    not match what the provider protocol requires.
    """


class AuthenticationError(ApiError):
    """Raised when API authentication fails (HTTP 401 or 403).

    Signals that the configured API key is invalid or expired.
    Callers should prompt the user to re-enter credentials rather than retrying.
    """

    def __init__(
        self,
        provider: str,
        status: int = 401,
        message: Optional[str] = None,
        body: Optional[str] = None,
    ) -> None:
        super().__init__(
            provider,
            status,
            message or f"{provider}: authentication failed (HTTP {status}) — check the API key",
            body=body,
        )
