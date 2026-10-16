"""Transport for NSW (New South Wales) provider — Trip Planner API (EFA rapidJSON).

Requires a free API key from https://opendata.transport.nsw.gov.au/, sent as
``Authorization: apikey <key>``.

Endpoints are the TfNSW flavour of EFA: ``/v1/tp/departure_mon`` and
``/v1/tp/stop_finder`` (not the ``XML_*_REQUEST`` names), with the TfNSW
specific ``TfNSWDM`` / ``TfNSWSF`` flags and ``version=10.2.1.42``.

Stop ids are TfNSW global stop ids (``200060`` Central Station,
``10101100`` legacy id of Central, ``2000338`` a single platform).
"""

import logging
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Union
from zoneinfo import ZoneInfo

from ..exceptions import ApiResponseError, AuthenticationError
from ..models import UnifiedDeparture
from .efa_base import EFABaseProvider
from ..const import PROVIDER_TFNSW

_LOGGER = logging.getLogger(__name__)


API_BASE = "https://api.transport.nsw.gov.au/v1/tp"
API_VERSION = "10.2.1.42"

# TfNSW product classes (differ from the German EFA classes).
_TRANSPORT_TYPES: Dict[Any, str] = {
    1: "train",  # Sydney Trains / NSW TrainLink
    2: "subway",  # Sydney Metro
    4: "tram",  # Light Rail
    5: "bus",
    7: "bus",  # Coach
    9: "ferry",
    11: "bus",  # School bus
}

_STOP_TYPES = {"stop", "platform", "station"}


def _tfnsw_realtime(stop: Dict[str, Any], estimated: Optional[str], planned: Optional[str]) -> bool:
    if stop.get("isRealtimeControlled") is True:
        return bool(estimated)
    return "MONITORED" in (stop.get("realtimeStatus") or [])


class TfNSWProvider(EFABaseProvider):
    """Transport for NSW (Sydney and New South Wales)."""

    @property
    def provider_id(self) -> str:
        return PROVIDER_TFNSW

    @property
    def provider_name(self) -> str:
        return "Transport for NSW"

    @property
    def requires_api_key(self) -> bool:
        return True

    @property
    def dm_base_url(self) -> str:
        return f"{API_BASE}/departure_mon"

    @property
    def sf_base_url(self) -> str:
        return f"{API_BASE}/stop_finder"

    def get_timezone(self) -> str:
        return "Australia/Sydney"

    def get_transport_type_mapping(self) -> Dict[Any, str]:
        return _TRANSPORT_TYPES

    def get_realtime_fn(self) -> Callable[[Dict[str, Any], Optional[str], Optional[str]], bool]:
        return _tfnsw_realtime

    def _headers(self) -> Dict[str, str]:
        if not self.api_key:
            raise AuthenticationError(self.provider_name, 401, message=f"{self.provider_name}: API key required")
        return {
            "Authorization": f"apikey {self.api_key}",
            "Accept": "application/json",
            "User-Agent": "OpenPublicTransport (python-openpublictransport)",
        }

    def departure_params(self, stop_id: str, now: datetime) -> Dict[str, str]:
        """Query parameters for departure_mon as documented by TfNSW."""
        local = now.astimezone(ZoneInfo(self.get_timezone()))
        return {
            "outputFormat": "rapidJSON",
            "coordOutputFormat": "EPSG:4326",
            "mode": "direct",
            "type_dm": "stop",
            "name_dm": stop_id,
            "depArrMacro": "dep",
            "itdDate": local.strftime("%Y%m%d"),
            "itdTime": local.strftime("%H%M"),
            "departureMonitorMacro": "true",
            "TfNSWDM": "true",
            "version": API_VERSION,
        }

    @staticmethod
    def stop_finder_params(search_term: str) -> Dict[str, str]:
        return {
            "outputFormat": "rapidJSON",
            "type_sf": "any",
            "name_sf": search_term,
            "coordOutputFormat": "EPSG:4326",
            "TfNSWSF": "true",
            "version": API_VERSION,
        }

    async def fetch_departures(
        self,
        station_id: Optional[str],
        place_dm: str,
        name_dm: str,
        departures_limit: int,
    ) -> Optional[Dict[str, Any]]:
        stop_id = station_id
        if not stop_id:
            matches = await self.search_stops(f"{name_dm} {place_dm}".strip()) if name_dm else []
            if not matches:
                return None
            stop_id = matches[0]["id"]

        data = await self._request(
            "get",
            self.dm_base_url,
            params=self.departure_params(stop_id, datetime.now(ZoneInfo(self.get_timezone()))),
            headers=self._headers(),
            retries=2,
            timeout=15,
        )
        if not isinstance(data, dict):
            raise ApiResponseError(f"{self.provider_name}: API returned {type(data).__name__} instead of an object")
        events = [e for e in data.get("stopEvents") or [] if isinstance(e, dict)]
        return {"stopEvents": events[:departures_limit]}

    def parse_departure(
        self, stop: Dict[str, Any], tz: Union[ZoneInfo, Any], now: datetime
    ) -> Optional[UnifiedDeparture]:
        dep = super().parse_departure(stop, tz, now)
        if dep is None:
            return None
        transportation = stop.get("transportation") or {}
        # Trains carry the long line name in "number" ("T1 North Shore & Western
        # Line"); the short designation ("T1") is in disassembledName.
        short = transportation.get("disassembledName")
        if short:
            if dep.line and dep.line != short and not dep.description:
                dep.description = dep.line
            dep.line = str(short)
        operator = (transportation.get("operator") or {}).get("name")
        if operator and not dep.agency:
            dep.agency = operator
        return dep

    async def search_stops(self, search_term: str) -> List[Dict[str, Any]]:
        term = search_term.strip()
        if not term:
            return []
        data = await self._request(
            "get",
            self.sf_base_url,
            params=self.stop_finder_params(term),
            headers=self._headers(),
            retries=2,
            timeout=10,
        )
        if not isinstance(data, dict):
            raise ApiResponseError(f"{self.provider_name}: API returned {type(data).__name__} instead of an object")
        return self.stops_from_finder(data)

    @staticmethod
    def stops_from_finder(data: Dict[str, Any]) -> List[Dict[str, Any]]:
        locations = [
            loc
            for loc in data.get("locations") or []
            if isinstance(loc, dict) and loc.get("id") and (loc.get("type") or "stop") in _STOP_TYPES
        ]
        locations.sort(key=lambda loc: (not loc.get("isBest"), -(loc.get("matchQuality") or 0)))
        results = []
        for loc in locations:
            parent = loc.get("parent") or {}
            place = parent.get("name") or ""
            if not place:
                full = loc.get("name") or ""
                if "," in full:
                    place = full.rsplit(",", 1)[1].strip()
            results.append(
                {
                    "id": str(loc["id"]),
                    "name": loc.get("disassembledName") or loc.get("name") or "",
                    "place": place,
                    "area_type": "stop",
                }
            )
        return results
