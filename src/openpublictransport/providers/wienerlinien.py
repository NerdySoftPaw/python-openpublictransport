"""Wiener Linien (Vienna) provider — OGD realtime monitor.

Data source: Stadt Wien – data.wien.gv.at, licensed CC BY 4.0. Display the
attribution ``"Datenquelle: Stadt Wien – data.wien.gv.at"`` wherever the data
is shown. The realtime API no longer requires a key.

Station ids are DIVA numbers (``60201320`` = Stephansplatz). A DIVA covers all
platforms of a station, so one id returns every line (U-Bahn, tram and bus).
Single platforms can be addressed by their RBL number instead (``4111``);
several ids may be combined with ``|``.
"""

import csv
import io
import logging
import unicodedata
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple, Union
from zoneinfo import ZoneInfo

from ..exceptions import ApiResponseError
from ..models import UnifiedDeparture
from .base import BaseProvider
from ..const import PROVIDER_WIENERLINIEN

_LOGGER = logging.getLogger(__name__)


ATTRIBUTION = "Datenquelle: Stadt Wien – data.wien.gv.at"

MONITOR_URL = "https://www.wienerlinien.at/ogd_realtime/monitor"
STATIONS_CSV_URL = "https://www.wienerlinien.at/ogd_realtime/doku/ogd/wienerlinien-ogd-haltestellen.csv"

TYPE_MAPPING: Dict[str, str] = {
    "ptMetro": "subway",
    "ptTram": "tram",
    "ptTramWLB": "tram",  # Badner Bahn (Wiener Lokalbahnen)
    "ptTramVRT": "tram",
    "ptBusCity": "bus",
    "ptBusNight": "bus",
    "ptBusRegion": "bus",
    "ptTrainS": "train",
    "ptTrain": "train",
}

# DIVA numbers are 8 digits (60200001…); RBL numbers are much shorter.
_DIVA_MIN_LEN = 7


def _normalize(text: str) -> str:
    """Casefold and strip diacritics so "strasse" matches "Straße"."""
    text = (text or "").casefold().replace("ß", "ss")
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def _parse_dt(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value:
        return None
    try:
        # "2026-10-16T20:00:00.000+0200" — fromisoformat accepts +0200 since 3.11
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _monitor_params(station_id: str) -> List[Tuple[str, str]]:
    params: List[Tuple[str, str]] = []
    for part in station_id.split("|"):
        part = part.strip()
        if not part:
            continue
        key = "diva" if len(part) >= _DIVA_MIN_LEN else "stopId"
        params.append((key, part))
    params.append(("activateTrafficInfo", "stoerungkurz"))
    return params


class WienerLinienProvider(BaseProvider):
    """Wiener Linien realtime departures (no API key required)."""

    attribution = ATTRIBUTION

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._stations: Optional[List[Dict[str, str]]] = None

    @property
    def provider_id(self) -> str:
        return PROVIDER_WIENERLINIEN

    @property
    def provider_name(self) -> str:
        return "Wiener Linien (Wien)"

    def get_timezone(self) -> str:
        return "Europe/Vienna"

    def get_transport_type_mapping(self) -> Dict[Any, str]:
        return TYPE_MAPPING

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

        data = await self._request("get", MONITOR_URL, params=_monitor_params(station_id), timeout=15, retries=2)
        if not isinstance(data, dict):
            raise ApiResponseError(f"{self.provider_name}: API returned {type(data).__name__} instead of an object")

        events = self.events_from_monitor(data)
        events.sort(key=lambda e: e["_sort"])
        return {"stopEvents": events[:departures_limit]}

    @staticmethod
    def events_from_monitor(data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Flatten monitors[].lines[].departures.departure[] into stop events."""
        body = data.get("data") or {}
        notices_by_line: Dict[str, List[str]] = {}
        for info in body.get("trafficInfos") or []:
            if not isinstance(info, dict):
                continue
            text = " ".join((info.get("title") or info.get("description") or "").split())
            if not text:
                continue
            for line in info.get("relatedLines") or []:
                bucket = notices_by_line.setdefault(str(line), [])
                if text not in bucket:
                    bucket.append(text)

        events: List[Dict[str, Any]] = []
        seen: set = set()
        for monitor in body.get("monitors") or []:
            if not isinstance(monitor, dict):
                continue
            for line in monitor.get("lines") or []:
                if not isinstance(line, dict):
                    continue
                for dep in (line.get("departures") or {}).get("departure") or []:
                    if not isinstance(dep, dict):
                        continue
                    times = dep.get("departureTime") or {}
                    vehicle = dep.get("vehicle") or {}
                    planned = times.get("timePlanned")
                    real = times.get("timeReal")
                    if not planned and not real:
                        continue
                    name = vehicle.get("name") or line.get("name") or ""
                    towards = vehicle.get("towards") or line.get("towards") or ""
                    platform = vehicle.get("platform") or line.get("platform") or ""
                    key = (name, towards, planned or real, platform)
                    if key in seen:
                        continue
                    seen.add(key)
                    sort_dt = _parse_dt(real) or _parse_dt(planned)
                    events.append(
                        {
                            "line": name,
                            "towards": towards,
                            "type": vehicle.get("type") or line.get("type") or "",
                            "platform": platform,
                            "barrierFree": vehicle.get("barrierFree", line.get("barrierFree")),
                            "timePlanned": planned,
                            "timeReal": real,
                            "countdown": times.get("countdown"),
                            "notices": notices_by_line.get(str(name)) or None,
                            "_sort": sort_dt.timestamp() if sort_dt else 0,
                        }
                    )
        return events

    def parse_departure(
        self, stop: Dict[str, Any], tz: Union[ZoneInfo, Any], now: datetime
    ) -> Optional[UnifiedDeparture]:
        try:
            planned = _parse_dt(stop.get("timePlanned"))
            real = _parse_dt(stop.get("timeReal"))
            if planned is None and real is None:
                return None
            planned_local = (planned or real).astimezone(tz)
            dep_local = (real or planned).astimezone(tz)
            delay = round((dep_local - planned_local).total_seconds() / 60)
            minutes_until = max(0, int((dep_local - now).total_seconds() / 60))

            towards = " ".join(str(stop.get("towards") or "").split()) or "Unknown"
            platform = str(stop.get("platform") or "") or None

            return UnifiedDeparture(
                line=str(stop.get("line") or ""),
                destination=towards,
                departure_time=dep_local.strftime("%H:%M"),
                planned_time=planned_local.strftime("%H:%M"),
                delay=delay,
                platform=platform,
                transportation_type=TYPE_MAPPING.get(stop.get("type") or "", "unknown"),
                is_realtime=real is not None,
                minutes_until_departure=minutes_until,
                departure_time_obj=dep_local,
                agency="Wiener Linien",
                notices=stop.get("notices") or None,
            )
        except Exception as exc:  # noqa: BLE001
            _LOGGER.debug("Error parsing Wiener Linien departure: %s", exc)
            return None

    @staticmethod
    def parse_stations_csv(text: str) -> List[Dict[str, str]]:
        """Parse wienerlinien-ogd-haltestellen.csv (DIVA;PlatformText;Municipality;…)."""
        reader = csv.DictReader(io.StringIO(text.lstrip("﻿")), delimiter=";")
        stations = []
        for row in reader:
            diva = (row.get("DIVA") or "").strip()
            name = (row.get("PlatformText") or "").strip()
            if diva and name:
                stations.append({"id": diva, "name": name, "place": (row.get("Municipality") or "").strip()})
        return stations

    async def _load_stations(self) -> List[Dict[str, str]]:
        if self._stations is None:
            text = await self._request("get", STATIONS_CSV_URL, response_format="text", timeout=20, retries=2)
            self._stations = self.parse_stations_csv(text or "")
        return self._stations

    @staticmethod
    def filter_stations(stations: List[Dict[str, str]], search_term: str, limit: int = 20) -> List[Dict[str, Any]]:
        tokens = _normalize(search_term).replace(",", " ").split()
        if not tokens:
            return []
        term = " ".join(tokens)
        scored = []
        for st in stations:
            haystack = _normalize(f"{st['name']} {st['place']}")
            if not all(t in haystack for t in tokens):
                continue
            name = _normalize(st["name"])
            score = 0 if name == term else 1 if name.startswith(term) else 2
            scored.append((score, len(name), st["name"], st))
        scored.sort(key=lambda x: x[:3])
        return [
            {"id": st["id"], "name": st["name"], "place": st["place"], "area_type": "stop"}
            for *_, st in scored[:limit]
        ]

    async def search_stops(self, search_term: str) -> List[Dict[str, Any]]:
        """Search stations in the static OGD stop list (downloaded once, filtered locally)."""
        return self.filter_stations(await self._load_stations(), search_term)
