"""Golemio PID (Prague Integrated Transport) provider.

Departures come from the Golemio API ``/v2/pid/departureboards`` and require a
free key (https://api.golemio.cz/api-keys/), sent as the ``x-access-token``
header.

Golemio has no fuzzy stop search, so stop search uses the public PID stop list
(https://data.pid.cz/stops/json/stops.json, no key, ~1.6 MB gzipped),
downloaded once per provider instance and filtered locally.

Station ids are ASW node numbers (``1072`` = Můstek): a node groups all
platforms of a stop incl. metro and train stations. Also accepted: a single
ASW stop ``1072/101`` / ``1072_101`` or a GTFS stop id ``U1072Z101P``; several
ids may be combined with ``|``.
"""

import logging
import re
import unicodedata
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple, Union
from zoneinfo import ZoneInfo

from ..exceptions import ApiResponseError, AuthenticationError
from ..models import UnifiedDeparture
from .base import BaseProvider
from ..const import PROVIDER_GOLEMIO

_LOGGER = logging.getLogger(__name__)


DEPARTURES_URL = "https://api.golemio.cz/v2/pid/departureboards"
STOPS_URL = "https://data.pid.cz/stops/json/stops.json"

# GTFS route types as used by PID.
ROUTE_TYPES: Dict[Any, str] = {
    0: "tram",
    1: "subway",
    2: "train",
    3: "bus",
    4: "ferry",
    7: "train",  # funicular (Petřín)
    11: "bus",  # trolleybus
}

_GTFS_ID_RE = re.compile(r"^U\d+[ZN]\d+", re.IGNORECASE)


def _normalize(text: str) -> str:
    text = (text or "").casefold()
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def _parse_dt(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def departure_params(station_id: str, limit: int) -> List[Tuple[str, str]]:
    """Build the departureboards query for one or more ``|``-separated ids."""
    params: List[Tuple[str, str]] = []
    for part in station_id.split("|"):
        part = part.strip()
        if not part:
            continue
        if _GTFS_ID_RE.match(part):
            params.append(("ids[]", part))
        else:
            params.append(("aswIds[]", part.replace("/", "_")))
    params += [
        ("limit", str(limit)),
        ("minutesBefore", "0"),
        ("minutesAfter", "180"),
        ("mode", "departures"),
        ("order", "real"),
        ("skip[]", "canceled"),
    ]
    return params


class GolemioProvider(BaseProvider):
    """PID Prague via Golemio."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._stops: Optional[List[Dict[str, str]]] = None

    @property
    def provider_id(self) -> str:
        return PROVIDER_GOLEMIO

    @property
    def provider_name(self) -> str:
        return "PID (Praha)"

    @property
    def requires_api_key(self) -> bool:
        return True

    def get_timezone(self) -> str:
        return "Europe/Prague"

    def get_transport_type_mapping(self) -> Dict[Any, str]:
        return ROUTE_TYPES

    def _headers(self) -> Dict[str, str]:
        if not self.api_key:
            raise AuthenticationError(self.provider_name, 401, message=f"{self.provider_name}: API key required")
        return {"x-access-token": self.api_key, "Accept": "application/json"}

    async def fetch_departures(
        self,
        station_id: Optional[str],
        place_dm: str,
        name_dm: str,
        departures_limit: int,
    ) -> Optional[Dict[str, Any]]:
        if station_id:
            params = departure_params(station_id, departures_limit)
        elif name_dm:
            # Exact (case-sensitive) stop name, as Golemio's names[] requires.
            params = [("names[]", name_dm), ("limit", str(departures_limit)), ("skip[]", "canceled")]
        else:
            return None

        data = await self._request(
            "get", DEPARTURES_URL, params=params, headers=self._headers(), timeout=15, retries=2
        )
        if not isinstance(data, dict):
            raise ApiResponseError(f"{self.provider_name}: API returned {type(data).__name__} instead of an object")
        return {"stopEvents": self.events_from_board(data)[:departures_limit]}

    @staticmethod
    def events_from_board(data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Attach the board's infotexts to every departure."""
        notices: List[str] = []
        for info in data.get("infotexts") or []:
            if isinstance(info, dict):
                text = " ".join((info.get("text") or info.get("text_en") or "").split())
                if text and text not in notices:
                    notices.append(text)
        events = []
        for dep in data.get("departures") or []:
            if isinstance(dep, dict):
                events.append({**dep, "_notices": notices or None})
        return events

    def parse_departure(
        self, stop: Dict[str, Any], tz: Union[ZoneInfo, Any], now: datetime
    ) -> Optional[UnifiedDeparture]:
        try:
            times = stop.get("departure_timestamp") or {}
            scheduled = _parse_dt(times.get("scheduled"))
            if scheduled is None:
                return None
            predicted = _parse_dt(times.get("predicted"))
            delay_info = stop.get("delay") or {}
            is_realtime = bool(delay_info.get("is_available"))

            planned_local = scheduled.astimezone(tz)
            dep_local = predicted.astimezone(tz) if predicted else planned_local

            if is_realtime and delay_info.get("minutes") is not None:
                delay = int(delay_info["minutes"])
            else:
                delay = round((dep_local - planned_local).total_seconds() / 60)
            minutes_until = max(0, int((dep_local - now).total_seconds() / 60))

            route = stop.get("route") or {}
            trip = stop.get("trip") or {}
            stop_ref = stop.get("stop") or {}

            transport_type = ROUTE_TYPES.get(route.get("type"), "unknown")
            notices = list(stop.get("_notices") or [])
            if trip.get("is_canceled"):
                notices.insert(0, "Spoj odřeknut / Trip cancelled")

            return UnifiedDeparture(
                line=str(route.get("short_name") or ""),
                destination=trip.get("headsign") or "Unknown",
                departure_time=dep_local.strftime("%H:%M"),
                planned_time=planned_local.strftime("%H:%M"),
                delay=delay,
                platform=stop_ref.get("platform_code") or None,
                transportation_type=transport_type,
                is_realtime=is_realtime,
                minutes_until_departure=minutes_until,
                departure_time_obj=dep_local,
                description=trip.get("short_name") or None,
                agency="PID",
                notices=notices or None,
            )
        except Exception as exc:  # noqa: BLE001
            _LOGGER.debug("Error parsing Golemio departure: %s", exc)
            return None

    @staticmethod
    def parse_stop_groups(data: Dict[str, Any]) -> List[Dict[str, str]]:
        """Reduce stops.json to one entry per ASW node."""
        result = []
        seen: set = set()
        for group in data.get("stopGroups") or []:
            if not isinstance(group, dict) or group.get("node") is None:
                continue
            node = str(group["node"])
            if node in seen:
                continue
            seen.add(node)
            result.append(
                {
                    "id": node,
                    "name": group.get("uniqueName") or group.get("name") or "",
                    "place": group.get("municipality") or "",
                }
            )
        return result

    async def _load_stops(self) -> List[Dict[str, str]]:
        if self._stops is None:
            data = await self._request("get", STOPS_URL, timeout=30, retries=2)
            if not isinstance(data, dict):
                raise ApiResponseError(f"{self.provider_name}: stop list is {type(data).__name__}, expected an object")
            self._stops = self.parse_stop_groups(data)
        return self._stops

    @staticmethod
    def filter_stops(stops: List[Dict[str, str]], search_term: str, limit: int = 20) -> List[Dict[str, Any]]:
        tokens = _normalize(search_term).replace(",", " ").split()
        if not tokens:
            return []
        term = " ".join(tokens)
        scored = []
        for st in stops:
            if not all(t in _normalize(f"{st['name']} {st['place']}") for t in tokens):
                continue
            name = _normalize(st["name"])
            score = 0 if name == term else 1 if name.startswith(term) else 2
            prague = 0 if st["place"] == "Praha" else 1
            scored.append((score, prague, len(name), st["name"], st))
        scored.sort(key=lambda x: x[:4])
        return [
            {"id": st["id"], "name": st["name"], "place": st["place"], "area_type": "stop"}
            for *_, st in scored[:limit]
        ]

    async def search_stops(self, search_term: str) -> List[Dict[str, Any]]:
        return self.filter_stops(await self._load_stops(), search_term)
