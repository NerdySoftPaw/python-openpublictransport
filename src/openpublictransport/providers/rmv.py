"""RMV (Rhein-Main-Verkehrsverbund) provider implementation."""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Union
from urllib.parse import quote
from zoneinfo import ZoneInfo

from ..const import PROVIDER_RMV
from ..exceptions import ApiResponseError, AuthenticationError
from ..models import UnifiedDeparture
from .base import BaseProvider

_LOGGER = logging.getLogger(__name__)

API_BASE = "https://www.rmv.de/hapi"

PRODUCT_MAPPING = {
    "ICE": "train",
    "IC": "train",
    "EC": "train",
    "RE": "train",
    "RB": "train",
    "S": "train",
    "U": "subway",
    "Tram": "tram",
    "Bus": "bus",
    "AST": "bus",
    "Fäh": "ferry",
}


def _parse_dt(s: str) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(s)
    except (ValueError, TypeError):
        return None


def _determine_transport_type(product: Dict[str, Any]) -> str:
    cat_out = product.get("catOut", "").strip()
    cat_code = product.get("catCode")

    for key, transport_type in PRODUCT_MAPPING.items():
        if cat_out.startswith(key):
            return transport_type

    if cat_code is not None:
        try:
            code = int(cat_code)
            if code in (1, 2):
                return "train"
            elif code in (4, 8):
                return "train"
            elif code == 16:
                return "train"
            elif code == 32:
                return "bus"
            elif code == 64:
                return "ferry"
            elif code == 128:
                return "subway"
            elif code == 256:
                return "tram"
        except (ValueError, TypeError):
            pass

    return "unknown"


class RMVProvider(BaseProvider):
    """RMV (Frankfurt/Rhine-Main) provider using HAFAS REST API."""

    @property
    def provider_id(self) -> str:
        return PROVIDER_RMV

    @property
    def provider_name(self) -> str:
        return "RMV (Frankfurt)"

    @property
    def requires_api_key(self) -> bool:
        return True

    def get_timezone(self) -> str:
        return "Europe/Berlin"

    async def fetch_departures(
        self,
        station_id: Optional[str],
        place_dm: str,
        name_dm: str,
        departures_limit: int,
    ) -> Optional[Dict[str, Any]]:
        if not station_id:
            _LOGGER.warning("RMV provider requires a station_id")
            return None

        if not self.api_key:
            raise AuthenticationError("RMV", 401, "RMV: an API key is required")

        url = (
            f"{API_BASE}/departureBoard"
            f"?accessId={self.api_key}"
            f"&id={station_id}"
            f"&format=json"
            f"&duration=120"
            f"&maxJourneys={departures_limit}"
        )

        data = await self._request("get", url, timeout=15)

        if not isinstance(data, dict):
            raise ApiResponseError(f"RMV: API returned {type(data).__name__} instead of an object")

        if "errorCode" in data:
            raise ApiResponseError(f"RMV: API error {data.get('errorCode')}: {data.get('errorText', 'unknown')}")

        departures = data.get("Departure", [])
        if isinstance(departures, dict):
            departures = [departures]
        return {"stopEvents": departures}

    def parse_departure(
        self, stop: Dict[str, Any], tz: Union[ZoneInfo, Any], now: datetime
    ) -> Optional[UnifiedDeparture]:
        try:
            date_str = stop.get("date")
            time_str = stop.get("time")
            rt_date_str = stop.get("rtDate")
            rt_time_str = stop.get("rtTime")

            if not date_str or not time_str:
                return None

            planned_dt_str = f"{date_str}T{time_str}"
            planned = _parse_dt(planned_dt_str)
            if not planned:
                return None
            planned_local = planned.replace(tzinfo=tz) if planned.tzinfo is None else planned.astimezone(tz)

            if rt_date_str and rt_time_str:
                rt_dt_str = f"{rt_date_str}T{rt_time_str}"
                rt = _parse_dt(rt_dt_str)
                if rt:
                    when_local = rt.replace(tzinfo=tz) if rt.tzinfo is None else rt.astimezone(tz)
                    is_realtime = True
                else:
                    when_local = planned_local
                    is_realtime = False
            else:
                when_local = planned_local
                is_realtime = False

            delay_minutes = int((when_local - planned_local).total_seconds() / 60)

            product = stop.get("ProductAtStop", stop.get("Product", {}))
            if isinstance(product, list):
                product = product[0] if product else {}
            line_name = product.get("line", product.get("name", ""))
            transport_type = _determine_transport_type(product)

            direction = stop.get("direction", "Unknown")
            platform = stop.get("track", "")

            rt_track = stop.get("rtTrack", "")
            planned_track = platform
            platform_changed = bool(rt_track and planned_track and rt_track != planned_track)
            if rt_track:
                platform = rt_track

            time_diff = when_local - now
            minutes_until = max(0, int(time_diff.total_seconds() / 60))

            notices = []
            for msg in stop.get("Messages", {}).get("Message", []):
                if isinstance(msg, dict):
                    text = msg.get("head", "") or msg.get("text", "")
                    if text:
                        notices.append(text)

            operator = product.get("operator", product.get("operatorName", ""))

            return UnifiedDeparture(
                line=line_name,
                destination=direction,
                departure_time=when_local.strftime("%H:%M"),
                planned_time=planned_local.strftime("%H:%M"),
                delay=delay_minutes,
                platform=platform,
                transportation_type=transport_type,
                is_realtime=is_realtime,
                minutes_until_departure=minutes_until,
                departure_time_obj=when_local,
                description=product.get("catOutL"),
                agency=operator if operator else None,
                notices=notices if notices else None,
                planned_platform=planned_track if platform_changed else None,
                platform_changed=platform_changed,
            )
        except Exception as e:
            _LOGGER.debug("Error parsing RMV departure: %s", e)
            return None

    async def search_stops(self, search_term: str) -> List[Dict[str, Any]]:
        if not self.api_key:
            raise AuthenticationError("RMV", 401, "RMV: an API key is required")

        url = (
            f"{API_BASE}/location.name"
            f"?accessId={self.api_key}"
            f"&input={quote(search_term, safe='')}"
            f"&format=json"
            f"&maxNo=15"
            f"&type=S"
        )

        data = await self._request("get", url, timeout=10)

        if not isinstance(data, dict):
            raise ApiResponseError(f"RMV: API returned {type(data).__name__} instead of an object")

        results = []
        for item in data.get("stopLocationOrCoordLocation", []):
            if not isinstance(item, dict):
                continue
            loc = item.get("StopLocation", {})
            if not loc:
                continue

            name = loc.get("name", "")
            place = ""
            if "," in name:
                parts = name.split(",", 1)
                place = parts[0].strip()

            results.append(
                {
                    "id": loc.get("extId", loc.get("id", "")),
                    "name": name,
                    "place": place,
                    "area_type": "stop",
                }
            )
        return results
