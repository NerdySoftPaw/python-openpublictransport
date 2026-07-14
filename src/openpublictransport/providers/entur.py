"""Entur (Norway) provider implementation.

Norway's national journey planner. Departures come from Entur's OTP
**transmodel** GraphQL API and stop search from Entur's geocoder. Both are
keyless; Entur only requires an identifying ``ET-Client-Name`` header.

  * geocoder    https://api.entur.io/geocoder/v1/autocomplete
  * journeys    https://api.entur.io/journey-planner/v3/graphql

Catalogued by public-transport/transport-apis as ``no/entur-otp``.
"""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Union
from urllib.parse import quote
from zoneinfo import ZoneInfo

import aiohttp

from ..const import PROVIDER_ENTUR_NO
from ..models import UnifiedDeparture
from .base import BaseProvider

_LOGGER = logging.getLogger(__name__)

GEOCODER_URL = "https://api.entur.io/geocoder/v1/autocomplete"
JOURNEY_PLANNER_URL = "https://api.entur.io/journey-planner/v3/graphql"
CLIENT_NAME = "nerdysoftpaw-openpublictransport"

# Transmodel transportMode (lower-case) -> unified transport type.
MODE_MAPPING = {
    "rail": "train",
    "coach": "bus",
    "bus": "bus",
    "trolleybus": "bus",
    "tram": "tram",
    "metro": "subway",
    "water": "ferry",
    "ferry": "ferry",
    "funicular": "train",
    "cableway": "tram",
    "monorail": "subway",
    "taxi": "taxi",
}

_DEPARTURES_QUERY = (
    "query($id:String!,$n:Int!){"
    "stopPlace(id:$id){"
    "estimatedCalls(numberOfDepartures:$n,timeRange:7200){"
    "aimedDepartureTime expectedDepartureTime realtime cancellation "
    "destinationDisplay{frontText} quay{publicCode} "
    "serviceJourney{line{publicCode transportMode}}"
    "}}}"
)


def _parse_dt(s: Optional[str]) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(s) if s else None
    except (ValueError, TypeError):
        return None


class EnturProvider(BaseProvider):
    """Entur (Norway) via transmodel GraphQL + Entur geocoder."""

    @property
    def provider_id(self) -> str:
        return PROVIDER_ENTUR_NO

    @property
    def provider_name(self) -> str:
        return "Entur (Norge)"

    def get_timezone(self) -> str:
        return "Europe/Oslo"

    def _headers(self) -> Dict[str, str]:
        return {"ET-Client-Name": CLIENT_NAME, "Accept": "application/json"}

    async def search_stops(self, search_term: str) -> List[Dict[str, Any]]:
        url = (
            f"{GEOCODER_URL}?text={quote(search_term, safe='')}"
            "&size=12&lang=en&layers=venue"
        )
        try:
            async with self.session.get(
                url, headers=self._headers(), timeout=aiohttp.ClientTimeout(total=10)
            ) as response:
                if response.status != 200:
                    _LOGGER.error("Entur geocoder returned status %s", response.status)
                    return []
                data = await response.json()
        except Exception as e:  # noqa: BLE001
            _LOGGER.error("Error searching Entur stops: %s", e)
            return []

        return self._stops_from_geocoder(data)

    @staticmethod
    def _stops_from_geocoder(data: Dict[str, Any]) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []
        for feature in data.get("features", []):
            props = feature.get("properties", {}) if isinstance(feature, dict) else {}
            stop_id = str(props.get("id") or "")
            # Only stop places have a departure board.
            if not stop_id.startswith("NSR:StopPlace:"):
                continue
            name = props.get("label") or props.get("name") or ""
            if not name:
                continue
            results.append(
                {
                    "id": stop_id,
                    "name": name,
                    "place": props.get("locality") or "",
                    "area_type": "stop",
                }
            )
        return results

    async def fetch_departures(
        self,
        station_id: Optional[str],
        place_dm: str,
        name_dm: str,
        departures_limit: int,
    ) -> Optional[Dict[str, Any]]:
        if not station_id:
            _LOGGER.warning("Entur provider requires a station_id")
            return None

        payload = {
            "query": _DEPARTURES_QUERY,
            "variables": {"id": station_id, "n": departures_limit},
        }
        try:
            async with self.session.post(
                JOURNEY_PLANNER_URL,
                json=payload,
                headers=self._headers(),
                timeout=aiohttp.ClientTimeout(total=15),
            ) as response:
                if response.status != 200:
                    _LOGGER.warning("Entur journey planner returned status %s", response.status)
                    return None
                data = await response.json()
        except aiohttp.ClientError as e:
            _LOGGER.warning("Entur journey planner request failed: %s", e)
            return None
        except Exception as e:  # noqa: BLE001
            _LOGGER.warning("Entur journey planner error: %s", e)
            return None

        stop_place = (data.get("data") or {}).get("stopPlace")
        if not stop_place:
            return {"stopEvents": []}
        return {"stopEvents": stop_place.get("estimatedCalls") or []}

    def parse_departure(
        self, stop: Dict[str, Any], tz: Union[ZoneInfo, Any], now: datetime
    ) -> Optional[UnifiedDeparture]:
        try:
            aimed = _parse_dt(stop.get("aimedDepartureTime"))
            if not aimed:
                return None
            expected = _parse_dt(stop.get("expectedDepartureTime")) or aimed

            planned_local = aimed.astimezone(tz)
            when_local = expected.astimezone(tz)
            delay_minutes = int((when_local - planned_local).total_seconds() / 60)

            line_info = (stop.get("serviceJourney") or {}).get("line") or {}
            line = line_info.get("publicCode") or ""
            mode = (line_info.get("transportMode") or "").lower()
            transport_type = MODE_MAPPING.get(mode, "unknown")

            destination = (stop.get("destinationDisplay") or {}).get("frontText") or "Unknown"
            platform = (stop.get("quay") or {}).get("publicCode") or ""

            minutes_until = max(0, int((when_local - now).total_seconds() / 60))

            notices = None
            if stop.get("cancellation"):
                notices = ["Cancelled"]

            return UnifiedDeparture(
                line=line,
                destination=destination,
                departure_time=when_local.strftime("%H:%M"),
                planned_time=planned_local.strftime("%H:%M"),
                delay=delay_minutes,
                platform=platform,
                transportation_type=transport_type,
                is_realtime=bool(stop.get("realtime")),
                minutes_until_departure=minutes_until,
                departure_time_obj=when_local,
                description=None,
                agency=None,
                notices=notices,
                planned_platform=None,
                platform_changed=False,
            )
        except Exception as e:  # noqa: BLE001
            _LOGGER.debug("Error parsing Entur departure: %s", e)
            return None
