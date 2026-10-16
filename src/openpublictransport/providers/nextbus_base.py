"""Generic NextBus / umo IQ public JSON feed provider.

API: ``https://retro.umoiq.com/service/publicJSONFeed?command=...&a=<agency>``
(no key). Commands used:

* ``routeList``   – all routes of the agency
* ``routeConfig`` – stops of one route (``tag``, public ``stopId``, ``title``)
* ``predictions`` – realtime predictions for a public ``stopId``

Stop search: NextBus has no search endpoint, so a stop index is built from
``routeList`` + one ``routeConfig`` per route (``&terse`` drops the path
geometry, which makes each response a few KB instead of hundreds). The index is
built lazily on the first search, cached on the provider instance and filtered
locally. For an agency with ~200 routes this is ~200 small requests run with a
concurrency of 4 — a one-off cost of a few seconds to a few tens of seconds.

Stop ids are the public ``stopId`` (e.g. ``"8134"``). Stops without a public
``stopId`` (arrival-only ``*_ar`` tags at loops) are not indexed.

JSON quirks: the feed is converted from XML, so ``predictions``, ``direction``,
``prediction`` and ``message`` are an object when there is one element and a
list otherwise — :func:`_as_list` normalises that.

Predictions are realtime estimates without a schedule, so every departure is
``is_realtime=True`` with ``delay=0`` and ``planned_time == departure_time``.
"""

import asyncio
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Union
from zoneinfo import ZoneInfo

from ..exceptions import ApiResponseError
from ..models import UnifiedDeparture
from .base import BaseProvider

_LOGGER = logging.getLogger(__name__)

NEXTBUS_API_BASE = "https://retro.umoiq.com/service/publicJSONFeed"


def _as_list(value: Any) -> List[Any]:
    """Normalise a NextBus "object or list" field to a list."""
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _destination_from_title(title: str) -> str:
    """'South - 510 Spadina towards Union Station' -> 'Union Station'."""
    if not title:
        return ""
    marker = " towards "
    idx = title.lower().find(marker)
    if idx >= 0:
        return title[idx + len(marker):].strip()
    return title.strip()


class NextBusBaseProvider(BaseProvider):
    """Base class for agencies served by the NextBus / umo IQ public feed."""

    #: NextBus agency tag, e.g. ``"ttc"``. Set by subclasses.
    agency: str = ""
    #: IANA timezone of the agency.
    timezone: str = "UTC"
    #: City name used as ``place`` in stop search results.
    place: str = ""
    #: Concurrency used while building the stop index.
    index_concurrency: int = 4
    #: Maximum number of stop search results.
    max_search_results: int = 50

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._stop_index: Optional[List[Dict[str, Any]]] = None
        self._index_lock: Optional[asyncio.Lock] = None

    def get_timezone(self) -> str:
        return self.timezone

    def transport_type_for_route(self, route_tag: str) -> str:
        """Return the unified transport type for a route. Default: bus."""
        return "bus"

    async def _command(self, command: str, timeout: int = 15, **params: str) -> Dict[str, Any]:
        query = {"command": command, "a": self.agency, **params}
        data = await self._request("get", NEXTBUS_API_BASE, params=query, timeout=timeout, retries=2)
        if not isinstance(data, dict):
            raise ApiResponseError(f"{self.provider_name}: API returned {type(data).__name__} instead of an object")
        error = data.get("Error")
        if error:
            content = error.get("content") if isinstance(error, dict) else str(error)
            raise ApiResponseError(f"{self.provider_name}: {str(content).strip()}")
        return data

    # ------------------------------------------------------------------ departures

    async def fetch_departures(
        self,
        station_id: Optional[str],
        place_dm: str,
        name_dm: str,
        departures_limit: int,
    ) -> Optional[Dict[str, Any]]:
        if not station_id:
            matches = await self.search_stops(name_dm)
            if not matches:
                return {"stopEvents": []}
            station_id = matches[0]["id"]

        data = await self._command("predictions", stopId=str(station_id))
        events = self.flatten_predictions(data)
        events.sort(key=lambda e: e.get("epochTime") or 0)
        return {"stopEvents": events[:departures_limit] if departures_limit else events}

    @staticmethod
    def flatten_predictions(data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Turn a ``predictions`` response into a flat list of raw events."""
        events: List[Dict[str, Any]] = []
        for block in _as_list(data.get("predictions")):
            if not isinstance(block, dict):
                continue
            notices = [
                m.get("text").strip()
                for m in _as_list(block.get("message"))
                if isinstance(m, dict) and m.get("text")
            ]
            for direction in _as_list(block.get("direction")):
                if not isinstance(direction, dict):
                    continue
                for pred in _as_list(direction.get("prediction")):
                    if not isinstance(pred, dict):
                        continue
                    try:
                        epoch = int(pred.get("epochTime"))
                    except (TypeError, ValueError):
                        continue
                    events.append(
                        {
                            "routeTag": block.get("routeTag", ""),
                            "routeTitle": block.get("routeTitle", ""),
                            "stopTitle": block.get("stopTitle", ""),
                            "agencyTitle": block.get("agencyTitle", ""),
                            "directionTitle": direction.get("title", ""),
                            "epochTime": epoch,
                            "minutes": pred.get("minutes"),
                            "isDeparture": pred.get("isDeparture") == "true",
                            "affectedByLayover": pred.get("affectedByLayover") == "true",
                            "dirTag": pred.get("dirTag", ""),
                            "branch": pred.get("branch", ""),
                            "vehicle": pred.get("vehicle", ""),
                            "notices": notices,
                        }
                    )
        return events

    def parse_departure(
        self, stop: Dict[str, Any], tz: Union[ZoneInfo, Any], now: datetime
    ) -> Optional[UnifiedDeparture]:
        try:
            epoch = stop.get("epochTime")
            if epoch is None:
                return None
            dep = datetime.fromtimestamp(int(epoch) / 1000, tz=tz)
            route_tag = str(stop.get("routeTag") or "")
            minutes_until = max(0, int((dep - now).total_seconds() // 60))
            notices = list(stop.get("notices") or [])
            if stop.get("affectedByLayover"):
                notices.append("Estimate affected by layover")
            return UnifiedDeparture(
                line=route_tag or stop.get("routeTitle", ""),
                destination=_destination_from_title(stop.get("directionTitle", "")) or "Unknown",
                departure_time=dep.strftime("%H:%M"),
                planned_time=dep.strftime("%H:%M"),
                delay=0,
                platform=None,
                transportation_type=self.transport_type_for_route(route_tag),
                is_realtime=True,
                minutes_until_departure=minutes_until,
                departure_time_obj=dep,
                description=stop.get("routeTitle") or None,
                agency=stop.get("agencyTitle") or None,
                notices=notices or None,
            )
        except Exception as e:  # noqa: BLE001
            _LOGGER.debug("Error parsing NextBus prediction: %s", e)
            return None

    # ------------------------------------------------------------------ stop search

    async def _build_stop_index(self) -> List[Dict[str, Any]]:
        route_list = await self._command("routeList")
        route_tags = [
            r.get("tag") for r in _as_list(route_list.get("route")) if isinstance(r, dict) and r.get("tag")
        ]
        semaphore = asyncio.Semaphore(self.index_concurrency)

        async def fetch_route(tag: str) -> List[Dict[str, Any]]:
            async with semaphore:
                try:
                    cfg = await self._command("routeConfig", r=tag, terse="")
                except Exception as e:  # noqa: BLE001 — one broken route must not kill search
                    _LOGGER.debug("%s: routeConfig %s failed: %s", self.provider_name, tag, e)
                    return []
            route = cfg.get("route") or {}
            stops = route.get("stop") if isinstance(route, dict) else None
            return [dict(s, _route=tag) for s in _as_list(stops) if isinstance(s, dict)]

        results = await asyncio.gather(*(fetch_route(t) for t in route_tags))
        by_id: Dict[str, Dict[str, Any]] = {}
        for stops in results:
            for s in stops:
                stop_id = s.get("stopId")
                if not stop_id:
                    continue
                entry = by_id.setdefault(
                    str(stop_id), {"id": str(stop_id), "name": (s.get("title") or "").strip(), "routes": []}
                )
                if s["_route"] not in entry["routes"]:
                    entry["routes"].append(s["_route"])
        if not by_id and route_tags:
            raise ApiResponseError(f"{self.provider_name}: could not load any route configuration")
        return sorted(by_id.values(), key=lambda e: e["name"])

    async def get_stop_index(self) -> List[Dict[str, Any]]:
        """Return the cached stop index, building it on first use."""
        if self._stop_index is not None:
            return self._stop_index
        if self._index_lock is None:
            self._index_lock = asyncio.Lock()
        async with self._index_lock:
            if self._stop_index is None:
                self._stop_index = await self._build_stop_index()
        return self._stop_index

    async def search_stops(self, search_term: str) -> List[Dict[str, Any]]:
        words = [w for w in (search_term or "").lower().replace(",", " ").split() if w]
        if not words:
            return []
        index = await self.get_stop_index()
        results = []
        for entry in index:
            name_l = entry["name"].lower()
            if entry["id"] == search_term.strip() or all(w in name_l for w in words):
                results.append(
                    {
                        "id": entry["id"],
                        "name": entry["name"],
                        "place": self.place,
                        "area_type": "stop",
                        "transport_modes": sorted({self.transport_type_for_route(r) for r in entry["routes"]}),
                    }
                )
                if len(results) >= self.max_search_results:
                    break
        return results
