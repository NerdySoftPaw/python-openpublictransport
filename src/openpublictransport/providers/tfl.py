"""Transport for London (TfL) Unified API provider.

Arrival predictions from ``/StopPoint/{id}/Arrivals``. An ``app_key`` is
optional: anonymous access works with a lower rate limit; when ``api_key`` is
set it is sent as the ``app_key`` query parameter.

TfL only publishes predicted times here, no timetable — departures are
therefore always realtime with a delay of 0.

Stop ids are NaPTAN ids (``940GZZLUOXC`` Oxford Circus Underground,
``490000173RC`` a single bus stop). Hubs (``HUBKGX``) and bus-stop groups
(``490G…``) have no arrivals of their own; they are resolved to their child
stop points and the arrivals are merged.
"""

import asyncio
import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Union
from urllib.parse import quote
from zoneinfo import ZoneInfo

from ..exceptions import ApiResponseError
from ..models import UnifiedDeparture
from .base import BaseProvider
from ..const import PROVIDER_TFL

_LOGGER = logging.getLogger(__name__)


API_BASE = "https://api.tfl.gov.uk"
SEARCH_MODES = "tube,bus,dlr,overground,elizabeth-line,tram"

MODE_MAPPING: Dict[str, str] = {
    "tube": "subway",
    "bus": "bus",
    "coach": "bus",
    "dlr": "train",
    "overground": "train",
    "elizabeth-line": "train",
    "national-rail": "train",
    "tram": "tram",
    "cable-car": "tram",
    "river-bus": "ferry",
    "river-tour": "ferry",
}

# Hubs and on-street stop pairs carry no predictions themselves.
_CONTAINER_PREFIXES = ("HUB", "490G")
_MAX_CHILDREN = 16
_PLATFORM_RE = re.compile(r"platform\s+(\w+)\s*$", re.IGNORECASE)


def _parse_dt(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _is_container(stop_id: str) -> bool:
    return stop_id.upper().startswith(_CONTAINER_PREFIXES)


def child_stop_ids(stop_point: Dict[str, Any]) -> List[str]:
    """Collect the arrival-capable descendants of a hub / stop group."""
    result: List[str] = []

    def walk(node: Dict[str, Any], depth: int) -> None:
        for child in node.get("children") or []:
            if not isinstance(child, dict):
                continue
            cid = child.get("naptanId") or child.get("id") or ""
            if not cid:
                continue
            if _is_container(cid):
                if depth < 3:
                    walk(child, depth + 1)
            elif child.get("lines") and cid not in result:
                result.append(cid)

    walk(stop_point, 0)
    return result[:_MAX_CHILDREN]


class TfLProvider(BaseProvider):
    """TfL (London) — Underground, buses, DLR, Overground, Elizabeth line, trams."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._children_cache: Dict[str, List[str]] = {}

    @property
    def provider_id(self) -> str:
        return PROVIDER_TFL

    @property
    def provider_name(self) -> str:
        return "TfL (London)"

    @property
    def requires_api_key(self) -> bool:
        return False

    def get_timezone(self) -> str:
        return "Europe/London"

    def get_transport_type_mapping(self) -> Dict[Any, str]:
        return MODE_MAPPING

    def _params(self, extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        params = dict(extra or {})
        if self.api_key:
            params["app_key"] = self.api_key
        return params

    async def _get(self, path: str, extra: Optional[Dict[str, str]] = None) -> Any:
        return await self._request(
            "get",
            f"{API_BASE}{path}",
            params=self._params(extra),
            headers={"Accept": "application/json"},
            timeout=15,
            retries=2,
        )

    async def _arrivals(self, stop_id: str) -> List[Dict[str, Any]]:
        data = await self._get(f"/StopPoint/{quote(stop_id, safe='')}/Arrivals")
        if data is None:
            return []
        if not isinstance(data, list):
            raise ApiResponseError(f"{self.provider_name}: arrivals returned {type(data).__name__}, expected a list")
        return [a for a in data if isinstance(a, dict)]

    async def _resolve_children(self, stop_id: str) -> List[str]:
        if stop_id not in self._children_cache:
            data = await self._get(f"/StopPoint/{quote(stop_id, safe='')}")
            self._children_cache[stop_id] = child_stop_ids(data) if isinstance(data, dict) else []
        return self._children_cache[stop_id]

    async def fetch_departures(
        self,
        station_id: Optional[str],
        place_dm: str,
        name_dm: str,
        departures_limit: int,
    ) -> Optional[Dict[str, Any]]:
        if not station_id:
            matches = await self.search_stops(name_dm) if name_dm else []
            if not matches:
                return None
            station_id = matches[0]["id"]

        ids: List[str] = []
        for part in station_id.split("|"):
            part = part.strip()
            if not part:
                continue
            if _is_container(part):
                ids.extend(c for c in await self._resolve_children(part) if c not in ids)
            elif part not in ids:
                ids.append(part)

        batches = await asyncio.gather(*(self._arrivals(i) for i in ids))
        arrivals = self.merge_arrivals([a for batch in batches for a in batch])
        return {"stopEvents": arrivals[:departures_limit]}

    @staticmethod
    def merge_arrivals(arrivals: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Drop terminating trains and duplicates, sort by expected arrival."""
        seen: set = set()
        result = []
        for a in arrivals:
            naptan = a.get("naptanId")
            if naptan and a.get("destinationNaptanId") == naptan:
                continue  # terminates here — nothing departs
            key = (a.get("id"), a.get("vehicleId"), naptan, a.get("expectedArrival"))
            if key in seen:
                continue
            seen.add(key)
            result.append(a)
        result.sort(key=lambda a: (a.get("expectedArrival") or "", a.get("timeToStation") or 0))
        return result

    def parse_departure(
        self, stop: Dict[str, Any], tz: Union[ZoneInfo, Any], now: datetime
    ) -> Optional[UnifiedDeparture]:
        try:
            expected = _parse_dt(stop.get("expectedArrival"))
            if expected is None:
                return None
            dep_local = expected.astimezone(tz)
            minutes_until = max(0, int((dep_local - now).total_seconds() / 60))

            platform_name = stop.get("platformName") or ""
            if platform_name.strip().lower() == "null":
                platform_name = ""
            match = _PLATFORM_RE.search(platform_name)
            platform = match.group(1) if match else (platform_name or None)

            destination = stop.get("destinationName") or stop.get("towards") or "Unknown"
            for suffix in (" Underground Station", " DLR Station", " Rail Station"):
                if destination.endswith(suffix):
                    destination = destination[: -len(suffix)]
                    break

            return UnifiedDeparture(
                line=str(stop.get("lineName") or stop.get("lineId") or ""),
                destination=destination,
                departure_time=dep_local.strftime("%H:%M"),
                planned_time=dep_local.strftime("%H:%M"),
                delay=0,
                platform=platform,
                transportation_type=MODE_MAPPING.get(stop.get("modeName") or "", "unknown"),
                is_realtime=True,
                minutes_until_departure=minutes_until,
                departure_time_obj=dep_local,
                description=stop.get("towards") or None,
                agency="TfL",
                platform_name=platform_name or None,
            )
        except Exception as exc:  # noqa: BLE001
            _LOGGER.debug("Error parsing TfL arrival: %s", exc)
            return None

    async def search_stops(self, search_term: str) -> List[Dict[str, Any]]:
        term = search_term.strip()
        if not term:
            return []
        data = await self._get(f"/StopPoint/Search/{quote(term, safe='')}", {"modes": SEARCH_MODES})
        if not isinstance(data, dict):
            raise ApiResponseError(f"{self.provider_name}: API returned {type(data).__name__} instead of an object")
        return self.stops_from_search(data)

    @staticmethod
    def stops_from_search(data: Dict[str, Any]) -> List[Dict[str, Any]]:
        results = []
        for match in data.get("matches") or []:
            if not isinstance(match, dict) or not match.get("id"):
                continue
            name = match.get("name") or ""
            towards = match.get("towards")
            if towards:
                name = f"{name} (towards {towards})"
            results.append(
                {
                    "id": str(match["id"]),
                    "name": name,
                    "place": "London",
                    "area_type": "stop",
                }
            )
        return results
