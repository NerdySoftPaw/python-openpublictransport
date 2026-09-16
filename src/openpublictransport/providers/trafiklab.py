"""Trafiklab (Sweden) provider implementation."""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Union
from urllib.parse import quote
from zoneinfo import ZoneInfo

from ..const import API_BASE_URL_TRAFIKLAB, PROVIDER_TRAFIKLAB_SE, TRAFIKLAB_TRANSPORTATION_TYPES
from ..exceptions import ApiResponseError, AuthenticationError
from ..models import UnifiedDeparture
from ..parsers import parse_departure_generic
from .base import BaseProvider

_LOGGER = logging.getLogger(__name__)


class TrafiklabProvider(BaseProvider):
    """Trafiklab (Sweden) provider."""

    @property
    def provider_id(self) -> str:
        return PROVIDER_TRAFIKLAB_SE

    @property
    def provider_name(self) -> str:
        return "Trafiklab (Sweden)"

    @property
    def requires_api_key(self) -> bool:
        return True

    def get_timezone(self) -> str:
        return "Europe/Stockholm"

    async def fetch_departures(
        self,
        station_id: Optional[str],
        place_dm: str,
        name_dm: str,
        departures_limit: int,
    ) -> Optional[Dict[str, Any]]:
        if not self.api_key:
            raise AuthenticationError("Trafiklab", 401, "Trafiklab: an API key is required")

        if not station_id:
            _LOGGER.error("Trafiklab requires a station ID")
            return None

        url = f"{API_BASE_URL_TRAFIKLAB}/departures/{station_id}"
        params = {"key": self.api_key}

        headers = {"User-Agent": "Mozilla/5.0 (compatible; OpenPublicTransport Trafiklab)"}

        json_data = await self._request(
            "get", url, params=params, headers=headers, timeout=10, retries=3
        )

        if not isinstance(json_data, dict):
            raise ApiResponseError(f"Trafiklab: API returned {type(json_data).__name__} instead of an object")

        if "departures" not in json_data:
            _LOGGER.debug("Trafiklab API response missing 'departures' field")
            return {"stopEvents": []}

        departures = json_data.get("departures", [])
        _LOGGER.debug("Trafiklab API returned %d departures", len(departures))
        stop_events = []

        stockholm_tz = ZoneInfo("Europe/Stockholm")
        now_stockholm = datetime.now(stockholm_tz)
        offset = now_stockholm.strftime("%z")
        offset_formatted = f"{offset[:3]}:{offset[3:]}"  # +0100 -> +01:00

        for dep in departures:
            if not isinstance(dep, dict):
                continue

            scheduled_time = dep.get("scheduled")
            realtime_time = dep.get("realtime")
            route = dep.get("route") or {}
            platform_data = dep.get("scheduled_platform") or dep.get("realtime_platform") or {}
            transport_mode = route.get("transport_mode", "BUS") if route else "BUS"

            destination_obj = route.get("destination") if route else None
            destination_name = (
                destination_obj.get("name", "Unknown")
                if isinstance(destination_obj, dict)
                else "Unknown"
            )

            if scheduled_time and "+" not in scheduled_time and "Z" not in scheduled_time:
                scheduled_time = f"{scheduled_time}{offset_formatted}"
            if realtime_time and "+" not in realtime_time and "Z" not in realtime_time:
                realtime_time = f"{realtime_time}{offset_formatted}"

            stop_event = {
                "departureTimePlanned": scheduled_time,
                "departureTimeEstimated": realtime_time or scheduled_time,
                "transportation": {
                    "number": route.get("designation", "") if route else "",
                    "description": (
                        (route.get("name") or route.get("direction", "")) if route else ""
                    ),
                    "destination": {"name": destination_name},
                    "product": {"class": 0},
                },
                "platform": {"name": platform_data.get("designation", "") if platform_data else ""},
                "realtimeStatus": ["MONITORED"] if dep.get("is_realtime") else [],
                "transportMode": transport_mode,
            }
            stop_events.append(stop_event)

        return {"stopEvents": stop_events}

    def parse_departure(
        self, stop: Dict[str, Any], tz: Union[ZoneInfo, Any], now: datetime
    ) -> Optional[UnifiedDeparture]:
        transport_mode = stop.get("transportMode", "BUS")
        transport_type = TRAFIKLAB_TRANSPORTATION_TYPES.get(transport_mode, "bus")

        return parse_departure_generic(
            stop,
            tz,
            now,
            get_transport_type_fn=lambda t: transport_type,
            get_platform_fn=lambda s: (
                s.get("platform", {}).get("name", "")
                if isinstance(s.get("platform"), dict)
                else str(s.get("platform", ""))
            ),
            get_realtime_fn=lambda s, est, plan: est != plan if est and plan else False,
        )

    async def search_stops(self, search_term: str) -> List[Dict[str, Any]]:
        if not self.api_key:
            raise AuthenticationError("Trafiklab", 401, "Trafiklab: an API key is required")

        encoded_search = quote(search_term, safe="")
        url = f"{API_BASE_URL_TRAFIKLAB}/stops/name/{encoded_search}"
        params = {"key": self.api_key}

        data = await self._request("get", url, params=params, timeout=10, retries=3)

        if not isinstance(data, dict):
            raise ApiResponseError(f"Trafiklab: API returned {type(data).__name__} instead of an object")

        stop_groups = data.get("stop_groups", [])
        results = []

        for stop_group in stop_groups:
            if not isinstance(stop_group, dict):
                continue

            stops = stop_group.get("stops", [])
            place = None
            if stops and isinstance(stops[0], dict):
                stop_name = stop_group.get("name", "")
                place = stop_name.split(",")[-1].strip() if "," in stop_name else None

            result = {
                "id": stop_group.get("id", ""),
                "name": stop_group.get("name", ""),
                "place": place or "",
                "area_type": stop_group.get("area_type", ""),
                "transport_modes": stop_group.get("transport_modes", []),
            }
            results.append(result)

        return results
