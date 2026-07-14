"""Base provider for legacy HAFAS "Scotty" web endpoints.

Many national and regional operators still expose the classic HAFAS query
interface at ``<base>/bin/``:

  * ``ajax-getstop.exe``  — stop/station finder, returns JSONP
  * ``stboard.exe``       — station departure board, returns XML (``L=vs_java3``)

Unlike the modern ``mgate.exe`` JSON gateway (which requires per-request
signing), these endpoints work with a plain HTTP GET and need no API key, which
makes them a good fit for a lightweight, dependency-free provider.

A concrete provider only needs to set ``hafas_base_url`` (and optionally tweak
the timezone, path segments or product mappings). Known working deployments:

  * ÖBB (Austria)        https://fahrplan.oebb.at/bin
  * Samtrafiken (Sweden) https://reseplanerare.resrobot.se/bin

See https://github.com/public-transport/transport-apis for a wider catalogue of
public transport endpoints. Note that dataset documents the modern
``mgate.exe`` protocol rather than this legacy interface, but it is a good
source of base URLs, timezones and product definitions for new subclasses.
"""

import html
import json
import logging
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple, Union
from urllib.parse import quote, urlencode
from zoneinfo import ZoneInfo

import aiohttp

from ..models import UnifiedDeparture
from .base import BaseProvider

_LOGGER = logging.getLogger(__name__)

# Product category codes (the token after ``#`` in a Journey's ``prod``
# attribute, e.g. ``"RJX 255#RJX"`` -> ``"RJX"``) mapped onto unified types.
# Keys are matched case-insensitively. Concrete providers may extend this.
DEFAULT_CATEGORY_MAPPING: Dict[str, str] = {
    # long-distance / high-speed trains
    "ICE": "train", "IC": "train", "EC": "train", "EN": "train", "NJ": "train",
    "D": "train", "RJ": "train", "RJX": "train", "TGV": "train", "THA": "train",
    "RR": "train", "WB": "train",
    # regional / local trains
    "IR": "train", "IRE": "train", "RE": "train", "RB": "train", "REX": "train",
    "CJX": "train", "R": "train", "S": "train", "SB": "train", "BRB": "train",
    "SPR": "train",  # NL Sprinter
    "TER": "train",  # French regional express (cross-border, e.g. LU/FR)
    # metro / underground
    "U": "subway", "U-BAHN": "subway", "UBAHN": "subway", "METRO": "subway",
    # tram
    "TRAM": "tram", "STR": "tram", "WLB": "tram",
    # bus
    "BUS": "bus", "O-BUS": "bus", "OBUS": "bus", "AST": "bus", "ASTBUS": "bus",
    "NB": "bus", "CB": "bus", "EXB": "bus",
    # water
    "F": "ferry", "FÄHRE": "ferry", "SCHIFF": "ferry", "FER": "ferry",
}

# Fallback: the HAFAS product-class bitmask carried on the ``class`` attribute
# (a single power of two) mapped onto unified types. Used when the category
# code is unknown or absent.
DEFAULT_CLASS_MAPPING: Dict[int, str] = {
    1: "train",    # nationalExpress (ICE/RJ/RJX)
    2: "train",    # national (EC/IC)
    4: "train",    # interregional (IC/IR)
    8: "train",    # night / EuroNight
    16: "train",   # regional express (REX/CJX)
    32: "train",   # S-Bahn / suburban
    64: "bus",
    128: "ferry",
    256: "subway",
    512: "tram",
    1024: "on_demand",
    2048: "on_demand",
}


# The station board is parsed with regexes rather than an XML parser: some
# deployments (e.g. LU/mobilitéit.lu) emit invalid XML — a literal ``<br>``
# inside an HIM ``lead="…"`` attribute — which makes a strict parser reject the
# whole document. We only need the well-formed ``<Journey …>`` opening-tag
# attributes; HIM notices are extracted best-effort.
_JOURNEY_RE = re.compile(r"<Journey\b([^>]*?)(?:/>|>(.*?)</Journey>)", re.S)
_ATTR_RE = re.compile(r'([\w:-]+)\s*=\s*"([^"]*)"')
_HIM_RE = re.compile(r"<HIMMessage\b([^>]*?)/?>", re.S)
_ERR_RE = re.compile(r'<Err\b[^>]*\bcode="([^"]*)"[^>]*(?:\btext="([^"]*)")?', re.S)


def _attrs(fragment: str) -> Dict[str, str]:
    """Parse ``key="value"`` pairs from a tag fragment, unescaping entities."""
    return {k: html.unescape(v) for k, v in _ATTR_RE.findall(fragment)}


def _line_from_prod(prod: str) -> str:
    """Derive a line label from a ``prod`` value (part before ``#``)."""
    return prod.split("#", 1)[0].strip() if prod else ""


def _parse_delay(raw: str) -> Tuple[int, bool, bool]:
    """Interpret a HAFAS ``delay`` attribute.

    Returns ``(delay_minutes, is_realtime, cancelled)``:
      * ``""`` / ``"-"``  -> no real-time prognosis
      * ``"cancel"``      -> cancelled (real-time)
      * ``"0"`` / ``"+ 5"`` / ``"-3"`` -> delay in minutes (real-time)
    """
    raw = (raw or "").strip()
    if not raw or raw == "-":
        return 0, False, False
    if raw.lower() == "cancel":
        return 0, True, True
    cleaned = raw.replace("+", "").replace(" ", "")
    try:
        return int(cleaned), True, False
    except ValueError:
        return 0, False, False


class HafasBaseProvider(BaseProvider):
    """Base class for legacy HAFAS "Scotty" providers.

    Subclasses set at least :attr:`hafas_base_url`.
    """

    #: Base URL up to and including ``/bin`` (no trailing slash),
    #: e.g. ``"https://fahrplan.oebb.at/bin"``.
    hafas_base_url: str = ""
    #: Path (relative to :attr:`hafas_base_url`) of the stop finder. The ``/dn``
    #: language segment selects the machine-readable JSONP output.
    stopfinder_path: str = "ajax-getstop.exe/dn"
    #: Path (relative to :attr:`hafas_base_url`) of the departure board.
    board_path: str = "stboard.exe/en"
    #: Optional products bitmask string; ``None`` lets the server return all.
    products_filter: Optional[str] = None
    #: IANA timezone the endpoint reports its local times in.
    timezone: str = "Europe/Berlin"

    def get_timezone(self) -> str:
        return self.timezone

    def get_category_mapping(self) -> Dict[str, str]:
        """Product category code -> unified transport type."""
        return DEFAULT_CATEGORY_MAPPING

    def get_class_mapping(self) -> Dict[int, str]:
        """HAFAS product-class bitmask -> unified transport type (fallback)."""
        return DEFAULT_CLASS_MAPPING

    # -- stop search --------------------------------------------------------

    async def search_stops(self, search_term: str) -> List[Dict[str, Any]]:
        url = (
            f"{self.hafas_base_url}/{self.stopfinder_path}"
            f"?REQ0JourneyStopsS0A=1&REQ0JourneyStopsS0G={quote(search_term, safe='')}&js=true"
        )
        try:
            async with self.session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as response:
                if response.status != 200:
                    _LOGGER.error(
                        "%s stop finder returned status %s", self.provider_name, response.status
                    )
                    return []
                text = await response.text()
        except Exception as e:  # noqa: BLE001 — network errors must not crash the search
            _LOGGER.error("Error searching %s stops: %s", self.provider_name, e)
            return []
        return self._parse_stopfinder(text)

    @staticmethod
    def _parse_stopfinder(text: str) -> List[Dict[str, Any]]:
        """Parse the ``SLs.sls={...};SLs.showSuggestion();`` JSONP payload."""
        match = re.search(r"SLs\.sls\s*=\s*(.+?);\s*SLs\.showSuggestion", text, re.S)
        if not match:
            return []
        try:
            data = json.loads(match.group(1))
        except (ValueError, json.JSONDecodeError):
            return []

        results: List[Dict[str, Any]] = []
        for suggestion in data.get("suggestions", []):
            if not isinstance(suggestion, dict):
                continue
            # type "1" == stop/station (2 == address, 4 == POI, ...)
            if str(suggestion.get("type")) != "1":
                continue
            ext_id = str(suggestion.get("extId") or "").strip()
            name = (suggestion.get("value") or "").strip()
            if not ext_id or not name:
                continue
            results.append(
                {
                    "id": ext_id,
                    "name": name,
                    "place": "",
                    "area_type": "stop",
                }
            )
        return results

    # -- departures ---------------------------------------------------------

    async def fetch_departures(
        self,
        station_id: Optional[str],
        place_dm: str,
        name_dm: str,
        departures_limit: int,
    ) -> Optional[Dict[str, Any]]:
        if not station_id:
            _LOGGER.warning("%s provider requires a station_id", self.provider_name)
            return None

        params = [
            ("L", "vs_java3"),
            ("boardType", "dep"),
            ("input", str(station_id)),
            ("maxJourneys", str(departures_limit)),
            ("start", "yes"),
        ]
        if self.products_filter:
            params.append(("productsFilter", self.products_filter))
        url = f"{self.hafas_base_url}/{self.board_path}?{urlencode(params)}"

        try:
            async with self.session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as response:
                if response.status != 200:
                    _LOGGER.warning(
                        "%s station board returned status %s", self.provider_name, response.status
                    )
                    return None
                raw = await response.read()
        except aiohttp.ClientError as e:
            _LOGGER.warning("%s station board request failed: %s", self.provider_name, e)
            return None
        except Exception as e:  # noqa: BLE001
            _LOGGER.warning("%s station board error: %s", self.provider_name, e)
            return None

        return {"stopEvents": self._parse_board(raw)}

    def _parse_board(self, raw: bytes) -> List[Dict[str, Any]]:
        # HAFAS Scotty boards are ISO-8859-1; decoding is lossless and never
        # raises, and entities in attribute values are unescaped in ``_attrs``.
        text = raw.decode("iso-8859-1", "replace")

        events = [
            self._journey_to_dict(m.group(1), m.group(2))
            for m in _JOURNEY_RE.finditer(text)
        ]

        if not events:
            err = _ERR_RE.search(text)
            if err:
                _LOGGER.warning(
                    "%s station board error %s: %s",
                    self.provider_name,
                    err.group(1),
                    err.group(2) or "",
                )
        return events

    @staticmethod
    def _journey_to_dict(opening: str, inner: Optional[str]) -> Dict[str, Any]:
        attr = _attrs(opening)
        notices: List[str] = []
        for him in _HIM_RE.finditer(inner or ""):
            ha = _attrs(him.group(1))
            note = (ha.get("header") or ha.get("lead") or "").strip()
            if note and note not in notices:
                notices.append(note)
        return {
            "fpTime": attr.get("fpTime", ""),
            "fpDate": attr.get("fpDate", ""),
            "delay": attr.get("delay", ""),
            "platform": attr.get("platform", ""),
            "prod": attr.get("prod", ""),
            "hafasname": attr.get("hafasname", ""),
            "dir": attr.get("dir", ""),
            "targetLoc": attr.get("targetLoc", ""),
            "class": attr.get("class", ""),
            "notices": notices,
        }

    def parse_departure(
        self, stop: Dict[str, Any], tz: Union[ZoneInfo, Any], now: datetime
    ) -> Optional[UnifiedDeparture]:
        try:
            date_str = stop.get("fpDate", "")
            time_str = stop.get("fpTime", "")
            if not date_str or not time_str:
                return None
            # Deployments differ: ÖBB reports a 4-digit year ("14.07.2026"),
            # NL/LU a 2-digit one ("14.07.26").
            planned_naive = None
            for fmt in ("%d.%m.%Y %H:%M", "%d.%m.%y %H:%M"):
                try:
                    planned_naive = datetime.strptime(f"{date_str} {time_str}", fmt)
                    break
                except ValueError:
                    continue
            if planned_naive is None:
                return None

            # fpTime/fpDate are already local wall-clock time for the endpoint.
            planned_local = planned_naive.replace(tzinfo=tz)

            delay_minutes, is_realtime, cancelled = _parse_delay(stop.get("delay", ""))
            when_local = planned_local + timedelta(minutes=delay_minutes)

            transport_type = self._transport_type(stop)
            line = stop.get("hafasname") or _line_from_prod(stop.get("prod", ""))
            destination = stop.get("dir") or stop.get("targetLoc") or "Unknown"
            platform = stop.get("platform") or ""

            minutes_until = max(0, int((when_local - now).total_seconds() / 60))

            notices = list(stop.get("notices") or [])
            if cancelled:
                notices.insert(0, "Cancelled")

            return UnifiedDeparture(
                line=line,
                destination=destination,
                departure_time=when_local.strftime("%H:%M"),
                planned_time=planned_local.strftime("%H:%M"),
                delay=delay_minutes,
                platform=platform,
                transportation_type=transport_type,
                is_realtime=is_realtime,
                minutes_until_departure=minutes_until,
                departure_time_obj=when_local,
                description=None,
                agency=None,
                notices=notices or None,
                planned_platform=None,
                platform_changed=False,
            )
        except Exception as e:  # noqa: BLE001
            _LOGGER.debug("Error parsing %s departure: %s", self.provider_name, e)
            return None

    def _transport_type(self, stop: Dict[str, Any]) -> str:
        prod = stop.get("prod", "")
        category = prod.rsplit("#", 1)[-1].strip().upper() if "#" in prod else ""
        mapping = self.get_category_mapping()
        if category and category in mapping:
            return mapping[category]

        try:
            cls = int(stop.get("class", "") or 0)
        except (ValueError, TypeError):
            cls = 0
        if cls:
            return self.get_class_mapping().get(cls, "unknown")
        return "unknown"
