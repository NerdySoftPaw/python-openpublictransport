"""Base class for EFA (Electronic Fahrplan-Auskunft) providers."""

import logging
from abc import abstractmethod
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple, Union
from urllib.parse import quote
from zoneinfo import ZoneInfo

from ..exceptions import ApiResponseError
from ..models import UnifiedDeparture
from ..parsers import parse_departure_generic
from .base import BaseProvider

_LOGGER = logging.getLogger(__name__)


def _transport_type_from_name(name: str) -> str:
    """Best-effort transport type from an EFA product name.

    Used as a fallback when the numeric product class is not in a provider's
    mapping (e.g. KVV Regionalbus class 6), so unmapped classes are not dropped
    as "unknown". Checks subway/tram before the generic "bahn" → train.
    """
    n = (name or "").lower()
    if "u-bahn" in n or "u_bahn" in n or "ubahn" in n or "subway" in n or "metro" in n:
        return "subway"
    if "straßenbahn" in n or "strassenbahn" in n or "stadtbahn" in n or "tram" in n:
        return "tram"
    if "fähre" in n or "faehre" in n or "schiff" in n or "ferry" in n:
        return "ferry"
    if "bus" in n or "ast" in n or "ruf" in n or "ersatz" in n:
        # Stadt-/Regional-/Schnell-/Nachtbus, AST, Rufbus, (Schienen-)Ersatzverkehr
        return "bus"
    if "bahn" in n or "zug" in n or "train" in n:  # S-Bahn, Regionalbahn, Zug, …
        return "train"
    return "unknown"


def _location_and_properties(stop: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Return (location, location.properties) from an EFA stop event."""
    location = stop.get("location")
    if not isinstance(location, dict):
        return {}, {}
    properties = location.get("properties")
    return location, properties if isinstance(properties, dict) else {}


def _efa_platform(stop: Dict[str, Any]) -> str:
    """Technical platform/track of an EFA stop event.

    EFA's RapidJSON carries the track under ``location.properties.platform``.
    Prefer that technical value ("3") over the human-readable ``platformName``
    / ``disassembledName`` ("Gleis 3"): the readable form is only populated for
    rail platforms, while bus and tram stops still have the technical
    identifier (issue #56).

    The last two fallbacks are the pre-RapidJSON shape, kept so older or
    non-standard EFA deployments keep working.
    """
    location, properties = _location_and_properties(stop)
    legacy = stop.get("platform")
    legacy_name = legacy.get("name") if isinstance(legacy, dict) else None
    return (
        properties.get("platform")
        or properties.get("platformName")
        or location.get("disassembledName")
        or legacy_name
        or stop.get("platformName")
        or ""
    )


def _efa_platform_name(stop: Dict[str, Any]) -> str:
    """Human-readable platform label ("Gleis 3"), when EFA supplies one."""
    location, properties = _location_and_properties(stop)
    return properties.get("platformName") or location.get("disassembledName") or ""


def _efa_planned_platform(stop: Dict[str, Any]) -> str:
    """Scheduled platform, for detecting a real-time platform change."""
    _, properties = _location_and_properties(stop)
    return properties.get("plannedPlatform") or properties.get("plannedPlatformName") or ""


class EFABaseProvider(BaseProvider):
    """Base class for all EFA-based providers (VRR, KVV, HVV, MVV, etc.)."""

    @property
    @abstractmethod
    def dm_base_url(self) -> str:
        """Return the base URL for departure monitor requests."""

    @property
    @abstractmethod
    def sf_base_url(self) -> str:
        """Return the base URL for stop finder requests."""

    def get_platform_fn(self) -> Callable[[Dict[str, Any]], str]:
        """Return function to extract the technical platform from a stop event."""
        return _efa_platform

    def get_platform_name_fn(self) -> Callable[[Dict[str, Any]], str]:
        """Return function to extract the human-readable platform label."""
        return _efa_platform_name

    def get_planned_platform_fn(self) -> Callable[[Dict[str, Any]], str]:
        """Return function to extract the scheduled platform."""
        return _efa_planned_platform

    def get_realtime_fn(self) -> Callable[[Dict[str, Any], Optional[str], Optional[str]], bool]:
        """Return function to detect realtime data."""
        return lambda s, est, plan: "MONITORED" in s.get("realtimeStatus", [])

    async def fetch_departures(
        self,
        station_id: Optional[str],
        place_dm: str,
        name_dm: str,
        departures_limit: int,
    ) -> Optional[Dict[str, Any]]:
        """Fetch departure data from EFA API."""
        if station_id:
            params = (
                f"outputFormat=RapidJSON&"
                f"stateless=1&"
                f"type_dm=any&"
                f"name_dm={station_id}&"
                f"mode=direct&"
                f"useRealtime=1&"
                f"limit={departures_limit}"
            )
        else:
            params = (
                f"outputFormat=RapidJSON&"
                f"place_dm={place_dm}&"
                f"type_dm=stop&"
                f"name_dm={name_dm}&"
                f"mode=direct&"
                f"useRealtime=1&"
                f"limit={departures_limit}"
            )

        url = f"{self.dm_base_url}?{params}"
        name = self.provider_name

        headers = {"User-Agent": f"Mozilla/5.0 (compatible; OpenPublicTransport {self.provider_id.upper()})"}

        # content_type=None: some deployments (VGN) send RapidJSON payloads with
        # a "text/xml;;charset=utf-8" header (issue #79).
        json_data = await self._request("get", url, headers=headers, retries=3, timeout=10)

        if not isinstance(json_data, dict):
            raise ApiResponseError(f"{name}: API returned {type(json_data).__name__} instead of an object")

        if "stopEvents" not in json_data:
            _LOGGER.debug("%s API response missing 'stopEvents' field", name)
            return {"stopEvents": []}

        return json_data

    def parse_departure(
        self, stop: Dict[str, Any], tz: Union[ZoneInfo, Any], now: datetime
    ) -> Optional[UnifiedDeparture]:
        """Parse a single departure from EFA API response."""
        type_mapping = self.get_transport_type_mapping()

        def determine_transport_type(transportation: Dict[str, Any]) -> str:
            product = transportation.get("product", {})
            product_class = product.get("class", 0)
            transport_type = type_mapping.get(product_class, "unknown")
            if transport_type == "unknown":
                # Fall back to the product name so unmapped classes aren't dropped.
                transport_type = _transport_type_from_name(product.get("name", ""))
            if transport_type == "unknown":
                _LOGGER.debug(
                    "Unknown transport class %s / name %r for line %s",
                    product_class,
                    product.get("name"),
                    transportation.get("number", "unknown"),
                )
            return transport_type

        return parse_departure_generic(
            stop,
            tz,
            now,
            get_transport_type_fn=determine_transport_type,
            get_platform_fn=self.get_platform_fn(),
            get_realtime_fn=self.get_realtime_fn(),
            get_planned_platform_fn=self.get_planned_platform_fn(),
            get_platform_name_fn=self.get_platform_name_fn(),
        )

    async def search_stops(self, search_term: str) -> List[Dict[str, Any]]:
        """Search for stops using EFA Stopfinder API."""
        if "," in search_term:
            parts = search_term.split(",", 1)
            stop_name = parts[0].strip()
            place_name = parts[1].strip()
            params = (
                f"outputFormat=RapidJSON&"
                f"locationServerActive=1&"
                f"type_sf=any&"
                f"name_sf={quote(stop_name, safe='')}&"
                f"place_sf={quote(place_name, safe='')}&"
                f"SpEncId=0"
            )
        else:
            params = (
                f"outputFormat=RapidJSON&"
                f"locationServerActive=1&"
                f"type_sf=stop&"
                f"name_sf={quote(search_term, safe='')}&"
                f"SpEncId=0"
            )

        url = f"{self.sf_base_url}?{params}"
        name = self.provider_name

        # content_type=None: some deployments (VGN) send RapidJSON payloads with
        # a "text/xml;;charset=utf-8" header (issue #79).
        data = await self._request("get", url, timeout=10)

        if not isinstance(data, dict):
            raise ApiResponseError(f"{name}: API returned {type(data).__name__} instead of an object")

        results = []
        for location in data.get("locations", []):
            if not isinstance(location, dict):
                continue

            disassembled_name = location.get("disassembledName", "")
            place = ""
            if "," in disassembled_name:
                parts = disassembled_name.rsplit(",", 1)
                place = parts[-1].strip() if len(parts) > 1 else ""

            results.append(
                {
                    "id": location.get("id", ""),
                    "name": location.get("name", ""),
                    "place": place,
                    "area_type": location.get("type", ""),
                }
            )

        return results
