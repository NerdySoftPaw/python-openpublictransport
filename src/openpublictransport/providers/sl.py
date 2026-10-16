"""SL Transport API provider (Storstockholms Lokaltrafik).

API docs: https://www.trafiklab.se/api/our-apis/sl/transport/ — free, no key.
"""

import logging
import re
import unicodedata
from datetime import datetime
from typing import Any, Dict, List, Optional, Union
from zoneinfo import ZoneInfo

from ..exceptions import ApiResponseError
from ..models import UnifiedDeparture
from .base import BaseProvider
from ..const import PROVIDER_SL_SE

_LOGGER = logging.getLogger(__name__)

PROVIDER_ID = PROVIDER_SL_SE
PROVIDER_NAME = "SL (Stockholm)"
TIMEZONE = "Europe/Stockholm"

API_BASE = "https://transport.integration.sl.se/v1"
FORECAST_MINUTES = 60
MAX_SEARCH_RESULTS = 20

TRANSPORT_MODE_MAPPING = {
    "BUS": "bus",
    "METRO": "subway",
    "TRAIN": "train",
    "TRAM": "tram",
    "SHIP": "ferry",
    "FERRY": "ferry",
}

_SOURCE_TZ = ZoneInfo(TIMEZONE)


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


def _parse_dt(value: Any) -> Optional[datetime]:
    """Parse an SL timestamp; naive values are Stockholm local time."""
    if not value or not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    return dt.replace(tzinfo=_SOURCE_TZ) if dt.tzinfo is None else dt


class SLProvider(BaseProvider):
    """SL Transport API provider for Stockholm public transport."""

    def __init__(self, *args: Any, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self._sites: Optional[List[Dict[str, Any]]] = None

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
        return dict(TRANSPORT_MODE_MAPPING)

    async def _load_sites(self) -> List[Dict[str, Any]]:
        if self._sites is None:
            data = await self._request("get", f"{API_BASE}/sites", timeout=20, retries=2)
            if not isinstance(data, list):
                raise ApiResponseError(f"{PROVIDER_NAME}: unexpected site list payload")
            self._sites = [s for s in data if isinstance(s, dict) and s.get("id") is not None]
        return self._sites

    async def search_stops(self, search_term: str) -> List[Dict[str, Any]]:
        term = _normalize(search_term)
        if not term:
            return []
        sites = await self._load_sites()

        scored = []
        for site in sites:
            name = site.get("name", "")
            candidates = [name, *(site.get("alias") or []), site.get("abbreviation") or ""]
            rank = _match_rank(term, candidates)
            if rank is not None:
                # Replacement-service sites ("ersättningstrafik") sort after the regular one.
                scored.append((rank, bool(site.get("note")), len(name), name, site))

        scored.sort(key=lambda item: item[:4])
        return [
            {
                "id": str(site["id"]),
                "name": site.get("name", ""),
                "place": site.get("note") or "",
                "area_type": "stop",
            }
            for *_, site in scored[:MAX_SEARCH_RESULTS]
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
            f"{API_BASE}/sites/{station_id}/departures",
            params={"forecast": FORECAST_MINUTES},
            timeout=15,
        )
        if not isinstance(data, dict):
            raise ApiResponseError(f"{PROVIDER_NAME}: API returned {type(data).__name__} instead of an object")

        events = data.get("departures") or []
        return {"stopEvents": events[: max(0, departures_limit)]}

    def parse_departure(
        self, stop: Dict[str, Any], tz: Union[ZoneInfo, Any], now: datetime
    ) -> Optional[UnifiedDeparture]:
        try:
            scheduled = _parse_dt(stop.get("scheduled"))
            expected = _parse_dt(stop.get("expected"))
            if scheduled is None and expected is None:
                return None
            planned_local = (scheduled or expected).astimezone(tz)
            dep_local = (expected or scheduled).astimezone(tz)
            # SL times carry seconds; compare at minute precision so delay matches the HH:MM shown.
            minute_diff = dep_local.replace(second=0, microsecond=0) - planned_local.replace(second=0, microsecond=0)
            delay_min = max(0, int(minute_diff.total_seconds() // 60))

            journey = stop.get("journey") or {}
            # Real-time when SL tracks the vehicle (journey.prediction_state set)
            # or the forecast deviates from the timetable.
            is_realtime = expected is not None and (
                bool(journey.get("prediction_state")) or (scheduled is not None and expected != scheduled)
            )

            line_info = stop.get("line") or {}
            mode = str(line_info.get("transport_mode") or "").upper()
            transport_type = TRANSPORT_MODE_MAPPING.get(mode, "unknown")

            notices: List[str] = []
            if str(stop.get("state", "")).upper() == "CANCELLED":
                notices.append("Cancelled")
            for deviation in stop.get("deviations") or []:
                if not isinstance(deviation, dict) or deviation.get("consequence") == "CANCELLED":
                    continue
                message = deviation.get("message")
                if message and message not in notices:
                    notices.append(message)

            platform = (stop.get("stop_point") or {}).get("designation") or None
            minutes_until = max(0, int((dep_local - now).total_seconds() / 60))

            return UnifiedDeparture(
                line=str(line_info.get("designation") or ""),
                destination=stop.get("destination") or stop.get("direction") or "Unknown",
                departure_time=dep_local.strftime("%H:%M"),
                planned_time=planned_local.strftime("%H:%M"),
                delay=delay_min,
                platform=platform,
                transportation_type=transport_type,
                is_realtime=is_realtime,
                minutes_until_departure=minutes_until,
                departure_time_obj=dep_local,
                description=line_info.get("group_of_lines") or None,
                agency="SL",
                notices=notices or None,
            )
        except Exception as e:  # noqa: BLE001
            _LOGGER.debug("Error parsing SL departure: %s", e)
            return None
