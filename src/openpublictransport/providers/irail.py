"""iRail provider (Belgian rail SNCB/NMBS).

API docs: https://docs.irail.be/ — free, no key, but a descriptive
User-Agent is requested and the rate limit is ~3 requests/second.
"""

import logging
import re
import unicodedata
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Union
from zoneinfo import ZoneInfo

from ..exceptions import ApiResponseError
from ..models import UnifiedDeparture
from .base import BaseProvider
from ..const import PROVIDER_IRAIL_BE

_LOGGER = logging.getLogger(__name__)

PROVIDER_ID = PROVIDER_IRAIL_BE
PROVIDER_NAME = "iRail (Belgien / SNCB)"
TIMEZONE = "Europe/Brussels"

API_BASE = "https://api.irail.be/v1"
USER_AGENT = "openpublictransport (https://github.com/NerdySoftPaw/openpublictransport)"
MAX_SEARCH_RESULTS = 20


def _normalize(text: str) -> str:
    """Casefold, strip accents and collapse punctuation to single spaces."""
    decomposed = unicodedata.normalize("NFKD", text or "")
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return re.sub(r"[^0-9a-z]+", " ", stripped.casefold()).strip()


def _match_rank(term: str, candidates: List[str]) -> Optional[int]:
    """Return 0 (exact), 1 (prefix), 2 (word prefix), 3 (substring) or None."""
    best: Optional[int] = None
    for cand in candidates:
        norm = _normalize(cand)
        if not norm:
            continue
        if norm == term:
            rank = 0
        elif norm.startswith(term):
            rank = 1
        elif (" " + norm).find(" " + term) != -1:
            rank = 2
        elif term in norm:
            rank = 3
        else:
            continue
        if best is None or rank < best:
            best = rank
    return best


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


class IRailProvider(BaseProvider):
    """iRail liveboard provider for SNCB/NMBS trains in Belgium."""

    def __init__(self, *args: Any, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self._stations: Optional[List[Dict[str, Any]]] = None

    @property
    def provider_id(self) -> str:
        return PROVIDER_ID

    @property
    def provider_name(self) -> str:
        return PROVIDER_NAME

    @property
    def requires_api_key(self) -> bool:
        return False

    def get_timezone(self) -> str:
        return TIMEZONE

    def get_transport_type_mapping(self) -> Dict[Any, str]:
        return {}

    @property
    def _headers(self) -> Dict[str, str]:
        return {"User-Agent": USER_AGENT, "Accept": "application/json"}

    async def _load_stations(self) -> List[Dict[str, Any]]:
        if self._stations is None:
            data = await self._request(
                "get",
                f"{API_BASE}/stations/",
                params={"format": "json", "lang": "en"},
                headers=self._headers,
                timeout=20,
                retries=2,
            )
            if not isinstance(data, dict) or not isinstance(data.get("station"), list):
                raise ApiResponseError(f"{PROVIDER_NAME}: unexpected station list payload")
            self._stations = [s for s in data["station"] if isinstance(s, dict) and s.get("id")]
        return self._stations

    async def search_stops(self, search_term: str) -> List[Dict[str, Any]]:
        term = _normalize(search_term)
        if not term:
            return []
        stations = await self._load_stations()

        scored = []
        for station in stations:
            name = station.get("name", "")
            standard = station.get("standardname", "")
            # standardname holds all language variants, e.g. "Brussel-Centraal/Bruxelles-Central"
            candidates = [name, standard, *standard.split("/")]
            rank = _match_rank(term, candidates)
            if rank is not None:
                scored.append((rank, len(name), name, station))

        scored.sort(key=lambda item: item[:3])
        return [
            {
                "id": str(station["id"]),
                "name": station.get("name", ""),
                "place": "",
                "area_type": "stop",
            }
            for _, _, _, station in scored[:MAX_SEARCH_RESULTS]
        ]

    async def fetch_departures(
        self,
        station_id: Optional[str],
        place_dm: str,
        name_dm: str,
        departures_limit: int,
    ) -> Optional[Dict[str, Any]]:
        if not station_id:
            matches = await self.search_stops(name_dm or place_dm)
            if not matches:
                return {"stopEvents": []}
            station_id = matches[0]["id"]

        data = await self._request(
            "get",
            f"{API_BASE}/liveboard/",
            params={"id": station_id, "format": "json", "lang": "en", "arrdep": "departure"},
            headers=self._headers,
            timeout=15,
        )
        if not isinstance(data, dict):
            raise ApiResponseError(f"{PROVIDER_NAME}: API returned {type(data).__name__} instead of an object")

        events = (data.get("departures") or {}).get("departure") or []
        if not isinstance(events, list):
            events = [events]
        return {"stopEvents": events[: max(0, departures_limit)]}

    def parse_departure(
        self, stop: Dict[str, Any], tz: Union[ZoneInfo, Any], now: datetime
    ) -> Optional[UnifiedDeparture]:
        try:
            epoch = stop.get("time")
            if epoch in (None, ""):
                return None
            # "time" is the scheduled departure (UTC epoch), "delay" is in seconds.
            planned_local = datetime.fromtimestamp(int(epoch), tz=ZoneInfo("UTC")).astimezone(tz)
            delay_s = _to_int(stop.get("delay"))
            dep_local = planned_local + timedelta(seconds=delay_s)
            delay_min = max(0, round(delay_s / 60))

            vehicle = stop.get("vehicleinfo") or {}
            line = vehicle.get("shortname") or vehicle.get("type") or ""
            destination = stop.get("station") or (stop.get("stationinfo") or {}).get("name") or "Unknown"

            platform_info = stop.get("platforminfo") or {}
            platform = stop.get("platform") or platform_info.get("name") or None
            platform_changed = str(platform_info.get("normal", "1")) == "0"

            notices = ["Cancelled"] if str(stop.get("canceled", "0")) == "1" else None

            minutes_until = max(0, int((dep_local - now).total_seconds() / 60))

            return UnifiedDeparture(
                line=line,
                destination=destination,
                departure_time=dep_local.strftime("%H:%M"),
                planned_time=planned_local.strftime("%H:%M"),
                delay=delay_min,
                platform=platform,
                transportation_type="train",
                # The iRail liveboard is fed by NMBS real-time data; a delay field means live info.
                is_realtime="delay" in stop,
                minutes_until_departure=minutes_until,
                departure_time_obj=dep_local,
                description=vehicle.get("type") or None,
                agency="SNCB/NMBS",
                notices=notices,
                planned_platform=None,
                platform_changed=platform_changed,
            )
        except Exception as e:  # noqa: BLE001
            _LOGGER.debug("Error parsing iRail departure: %s", e)
            return None
