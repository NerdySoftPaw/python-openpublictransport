"""Base class for all public transport providers."""

import asyncio
import logging

import aiohttp
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, Dict, List, Optional, Union
from zoneinfo import ZoneInfo

from ..exceptions import (
    ApiConnectionError,
    ApiError,
    ApiResponseError,
    ApiTimeoutError,
    AuthenticationError,
)
from ..models import UnifiedDeparture

_LOGGER = logging.getLogger(__name__)


class BaseProvider(ABC):
    """Abstract base class for all public transport providers."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        api_key: Optional[str] = None,
        api_key_secondary: Optional[str] = None,
        custom_url: Optional[str] = None,
    ):
        self.session = session
        self.api_key = api_key
        self.api_key_secondary = api_key_secondary
        self.custom_url = custom_url

    async def _request(
        self,
        method: str,
        url: str,
        *,
        retries: int = 1,
        timeout: int = 10,
        response_format: str = "json",
        json_content_type: Optional[str] = None,
        **kwargs: Any,
    ) -> Any:
        """Perform an HTTP request, returning the decoded payload or raising.

        This is the single place where HTTP status codes are turned into
        exceptions. Providers must not swallow the result: an empty list or
        ``None`` returned by a provider means "no data", never "request failed".

        Args:
            method: ``"get"`` or ``"post"``.
            url: Absolute request URL.
            retries: Total number of attempts. Only 5xx responses, timeouts and
                connection errors are retried, with ``2 ** attempt`` backoff.
                4xx responses fail immediately — retrying a bad key or a wrong
                endpoint only delays the error the user needs to see.
            timeout: Total request timeout in seconds.
            response_format: ``"json"``, ``"text"`` or ``"bytes"``.
            json_content_type: Passed to ``response.json()``. The default
                ``None`` disables aiohttp's strict content-type check, because
                some EFA deployments send RapidJSON with an XML header (#79).
            **kwargs: Forwarded to the aiohttp session method (``params``,
                ``headers``, ``data``, ``json``, ...).

        Returns:
            The decoded payload, or ``None`` for an empty 204 response.

        Raises:
            AuthenticationError: HTTP 401 or 403. Never retried.
            ApiError: Any other non-2xx status, carrying ``.status``.
            ApiTimeoutError: The request timed out on every attempt.
            ApiConnectionError: The provider could not be reached.
            ApiResponseError: A 2xx response whose body could not be decoded.
        """
        request = getattr(self.session, method.lower())
        name = self.provider_name
        last_error: Optional[Exception] = None

        for attempt in range(1, retries + 1):
            try:
                async with request(url, timeout=aiohttp.ClientTimeout(total=timeout), **kwargs) as response:
                    status = response.status

                    if status in (401, 403):
                        raise AuthenticationError(name, status, body=await self._error_body(response))

                    if status >= 500:
                        last_error = ApiError(name, status, body=await self._error_body(response))
                    elif status >= 400:
                        raise ApiError(name, status, body=await self._error_body(response))
                    elif status == 204:
                        return None
                    else:
                        return await self._decode_response(response, response_format, json_content_type)

            except asyncio.TimeoutError as exc:
                last_error = ApiTimeoutError(f"{name}: request timed out after {timeout}s")
                last_error.__cause__ = exc
            except aiohttp.ClientError as exc:
                last_error = ApiConnectionError(f"{name}: connection failed ({exc})")
                last_error.__cause__ = exc

            if attempt < retries:
                _LOGGER.debug("%s: attempt %s/%s failed (%s), retrying", name, attempt, retries, last_error)
                await asyncio.sleep(2**attempt)

        raise last_error if last_error else ApiConnectionError(f"{name}: request failed")

    @staticmethod
    async def _error_body(response: Any, limit: int = 300) -> Optional[str]:
        """Return a truncated error body, or None if it cannot be read.

        Providers often explain a rejection only in the body (a SOAP fault, a
        JSON ``errorText``), so it is worth carrying into the exception.
        """
        try:
            text = (await response.text() or "").strip()
        except Exception:  # noqa: BLE001 — diagnostics must never mask the real error
            return None
        if not text:
            return None
        return text[:limit] + ("…" if len(text) > limit else "")

    async def _decode_response(
        self,
        response: Any,
        response_format: str,
        json_content_type: Optional[str],
    ) -> Any:
        """Decode a successful response, raising ApiResponseError on bad payloads."""
        try:
            if response_format == "json":
                return await response.json(content_type=json_content_type)
            if response_format == "text":
                return await response.text()
            if response_format == "bytes":
                return await response.read()
        except (ValueError, aiohttp.ClientError) as exc:
            raise ApiResponseError(f"{self.provider_name}: could not decode response ({exc})") from exc

        raise ValueError(f"Unknown response_format: {response_format}")

    @property
    @abstractmethod
    def provider_id(self) -> str:
        """Return the provider identifier (e.g., 'vrr', 'kvv')."""
        pass

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Return the human-readable provider name."""
        pass

    @property
    def requires_api_key(self) -> bool:
        """Return True if this provider requires an API key."""
        return False

    @abstractmethod
    async def fetch_departures(
        self,
        station_id: Optional[str],
        place_dm: str,
        name_dm: str,
        departures_limit: int,
    ) -> Optional[Dict[str, Any]]:
        """Fetch departure data from the provider's API."""
        pass

    @abstractmethod
    def parse_departure(
        self, stop: Dict[str, Any], tz: Union[ZoneInfo, Any], now: datetime
    ) -> Optional[UnifiedDeparture]:
        """Parse a single departure from the provider's API response."""
        pass

    @abstractmethod
    async def search_stops(self, search_term: str) -> List[Dict[str, Any]]:
        """Search for stops/stations."""
        pass

    def get_timezone(self) -> str:
        """Return the timezone for this provider (e.g., 'Europe/Berlin')."""
        return "Europe/Berlin"

    def get_transport_type_mapping(self) -> Dict[Any, str]:
        """Return the transportation type mapping for this provider."""
        return {}

    async def cleanup(self) -> None:
        """Cleanup provider resources."""
        pass
