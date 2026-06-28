"""Rejseplanen (Denmark) provider using the HAFAS REST API.

Requires a free API key from labs.rejseplanen.dk (50k calls/month, non-commercial).
"""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Union
from urllib.parse import quote
from zoneinfo import ZoneInfo

import aiohttp

from ..const import PROVIDER_REJSEPLANEN
from ..exceptions import AuthenticationError
from ..models import UnifiedDeparture
from .base import BaseProvider

_LOGGER = logging.getLogger(__name__)

_API_BASE = "https://www.rejseplanen.dk/api"

_PRODUCT_MAPPING: Dict[str, str] = {
    "IC": "train",
    "LYN": "train",
    "RE": "train",
    "REG": "train",
    "S": "train",
    "TOG": "train",
    "M": "subway",
    "BUS": "bus",
    "EXB": "bus",
    "NB": "bus",
    "TB": "bus",
    "F": "ferry",
    "T": "tram",
}


def _parse_hafas_time(date_str: str, time_str: str, tz: Any) -> Optional[datetime]:
    """Parse a Rejseplanen date + time into a timezone-aware datetime.

    The HAFAS REST API at www.rejseplanen.dk returns ISO dates (YYYY-MM-DD) and
    times as HH:MM:SS; older HAFAS deployments used DD.MM.YY / HH:MM. Handle both.
    """
    if not date_str or not time_str:
        return None
    time_fmt = "%H:%M:%S" if time_str.count(":") == 2 else "%H:%M"
    date_fmt = "%Y-%m-%d" if "-" in date_str else "%d.%m.%y"
    try:
        dt = datetime.strptime(f"{date_str} {time_str}", f"{date_fmt} {time_fmt}")
        return dt.replace(tzinfo=tz)
    except ValueError:
        return None


def _resolve_transport_type(stop: Dict[str, Any]) -> str:
    """Derive the transport type from the HAFAS product category.

    The departure's top-level ``type`` is a stop type (e.g. "ST"), not the
    product, so read the category from ``Product[].catOut`` and fall back to the
    line name (e.g. "Bus 250S", "Metro M4", "Re 1277").
    """
    products = stop.get("Product", [])
    if isinstance(products, dict):
        products = [products]
    cat = ""
    if products and isinstance(products[0], dict):
        p = products[0]
        cat = (p.get("catOut") or p.get("catOutL") or p.get("catIn") or "").strip()
    text = f"{cat} {stop.get('name', '')}".lower()
    if "bus" in text:  # Bus, Togbus (rail-replacement), Natbus, Expresbus
        return "bus"
    if "metro" in text:
        return "subway"
    if "letbane" in text or "tram" in text:
        return "tram"
    if "færge" in text or "ferry" in text or "havnebus" in text:
        return "ferry"
    # Everything else (IC, ICL, Lyn, Re, Reg, Tog, S-tog, RJ, EC/ECE, …) is rail.
    return "train"


class RejseplanenProvider(BaseProvider):
    """Rejseplanen (Denmark) provider via HAFAS REST API."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        api_key: Optional[str] = None,
        api_key_secondary: Optional[str] = None,
        custom_url: Optional[str] = None,
    ):
        super().__init__(session, api_key=api_key, api_key_secondary=api_key_secondary, custom_url=custom_url)

    @property
    def provider_id(self) -> str:
        return PROVIDER_REJSEPLANEN

    @property
    def provider_name(self) -> str:
        return "Rejseplanen (Denmark)"

    @property
    def requires_api_key(self) -> bool:
        return True

    def get_timezone(self) -> str:
        return "Europe/Copenhagen"

    def get_transport_type_mapping(self) -> Dict:
        return _PRODUCT_MAPPING

    async def fetch_departures(
        self,
        station_id: Optional[str],
        place_dm: str,
        name_dm: str,
        departures_limit: int,
    ) -> Optional[Dict[str, Any]]:
        """Fetch departures from the Rejseplanen departureBoard endpoint."""
        if not station_id:
            _LOGGER.warning("%s: station_id required", self.provider_name)
            return None
        if not self.api_key:
            _LOGGER.error("%s: API key required", self.provider_name)
            return None

        url = (
            f"{_API_BASE}/departureBoard"
            f"?accessId={self.api_key}"
            f"&id={station_id}"
            f"&format=json"
            f"&duration=120"
            f"&maxJourneys={departures_limit}"
        )
        try:
            async with self.session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status in (401, 403):
                    raise AuthenticationError(
                        f"{self.provider_name}: authentication failed (HTTP {resp.status}) — check API key"
                    )
                if resp.status != 200:
                    _LOGGER.warning("%s: HTTP %s for station %s", self.provider_name, resp.status, station_id)
                    return None
                data = await resp.json(content_type=None)
        except Exception as exc:
            _LOGGER.warning("%s: request failed: %s", self.provider_name, exc)
            return None

        if not isinstance(data, dict):
            return None
        if "errorCode" in data:
            _LOGGER.warning("%s: API error %s: %s", self.provider_name, data.get("errorCode"), data.get("errorText"))
            return None

        # www.rejseplanen.dk returns "Departure" at the top level; older HAFAS
        # deployments nested it under "DepartureBoard". Support both.
        departures = data.get("Departure")
        if departures is None:
            departures = data.get("DepartureBoard", {}).get("Departure", [])
        if isinstance(departures, dict):
            departures = [departures]

        return {"stopEvents": departures}

    def parse_departure(
        self,
        stop: Dict[str, Any],
        tz: Union[ZoneInfo, Any],
        now: datetime,
    ) -> Optional[UnifiedDeparture]:
        """Parse a single Rejseplanen departure dict into UnifiedDeparture."""
        try:
            date_str = stop.get("date", "")
            time_str = stop.get("time", "")
            rt_date_str = stop.get("rtDate", "")
            rt_time_str = stop.get("rtTime", "")

            planned_dt = _parse_hafas_time(date_str, time_str, tz)
            if planned_dt is None:
                return None

            if rt_date_str and rt_time_str:
                actual_dt = _parse_hafas_time(rt_date_str, rt_time_str, tz) or planned_dt
                is_realtime = True
            else:
                actual_dt = planned_dt
                is_realtime = False

            delay = max(0, int((actual_dt - planned_dt).total_seconds() / 60))
            minutes_until = max(0, int((actual_dt - now).total_seconds() / 60))

            transport_type = _resolve_transport_type(stop)

            line = stop.get("name", "")
            destination = stop.get("direction", stop.get("finalStop", ""))

            track = stop.get("track", "") or ""
            rt_track = stop.get("rtTrack", "") or ""
            platform = rt_track if rt_track else track
            platform_changed = bool(rt_track and track and rt_track != track)

            notices: List[str] = []
            if stop.get("cancelled") == "true" or stop.get("cancelled") is True:
                notices.append("Cancelled / Aflyst")

            for msg in stop.get("Messages", {}).get("Message", []):
                if isinstance(msg, dict):
                    text = msg.get("head", "") or msg.get("text", "")
                    if text:
                        notices.append(text)

            return UnifiedDeparture(
                line=line,
                destination=destination,
                departure_time=actual_dt.strftime("%H:%M"),
                planned_time=planned_dt.strftime("%H:%M"),
                delay=delay,
                platform=platform or None,
                transportation_type=transport_type,
                is_realtime=is_realtime,
                minutes_until_departure=minutes_until,
                departure_time_obj=actual_dt,
                notices=notices if notices else None,
                planned_platform=track if platform_changed else None,
                platform_changed=platform_changed,
            )
        except Exception as exc:
            _LOGGER.debug("%s: parse error: %s", self.provider_name, exc)
            return None

    async def search_stops(self, search_term: str) -> List[Dict[str, Any]]:
        """Search for Danish stops using the Rejseplanen location.name endpoint."""
        if not self.api_key:
            _LOGGER.error("%s: API key required for stop search", self.provider_name)
            return []

        url = (
            f"{_API_BASE}/location.name"
            f"?accessId={self.api_key}"
            f"&input={quote(search_term, safe='')}"
            f"&format=json"
            f"&maxNo=15"
            f"&type=S"
        )
        try:
            async with self.session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    _LOGGER.warning("%s: stop search HTTP %s", self.provider_name, resp.status)
                    return []
                data = await resp.json(content_type=None)
        except Exception as exc:
            _LOGGER.warning("%s: stop search failed: %s", self.provider_name, exc)
            return []

        if not isinstance(data, dict):
            return []

        # www.rejseplanen.dk returns "stopLocationOrCoordLocation" (a list whose
        # entries wrap a StopLocation or CoordLocation); older HAFAS deployments
        # used LocationList.StopLocation. Support both.
        entries = data.get("stopLocationOrCoordLocation")
        if entries is None:
            entries = data.get("LocationList", {}).get("StopLocation", [])
        if isinstance(entries, dict):
            entries = [entries]

        results = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            loc = entry.get("StopLocation", entry)
            if not isinstance(loc, dict):
                continue
            station_id = loc.get("extId") or loc.get("id", "")
            name = loc.get("name", "")
            if not station_id or not name:
                continue
            results.append({"id": str(station_id), "name": name, "place": "", "area_type": "stop"})

        return results[:10]
