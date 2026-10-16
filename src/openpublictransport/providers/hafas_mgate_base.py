"""Base provider for the modern HAFAS ``mgate.exe`` JSON gateway.

The mgate protocol is a POST JSON API. A request wraps one or more service
calls (``LocMatch`` for stop search, ``StationBoard`` for departures) together
with an ``auth`` (access id), a ``client`` descriptor and a protocol ``ver``.
Responses use an indirection model: each journey references shared objects
(products, locations, remarks) by index into ``svcResL[i].res.common``.

Some deployments additionally require a signed request (an MD5 ``checksum`` or a
``mic``/``mac`` pair derived from a salt). That is supported here via
:attr:`checksum_salt` / :attr:`mic_mac_salt`, but every endpoint we currently
ship is **unsigned** (no salt in the public-transport/transport-apis catalogue).

A concrete provider sets :attr:`mgate_endpoint`, :attr:`mgate_auth`,
:attr:`mgate_client`, :attr:`mgate_ver` and :attr:`timezone`. See
public-transport/transport-apis for the per-operator config values.
"""

import hashlib
import json
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Union
from zoneinfo import ZoneInfo

from ..exceptions import ApiResponseError
from ..models import UnifiedDeparture
from .hafas_base import DEFAULT_CATEGORY_MAPPING
from .base import BaseProvider

_LOGGER = logging.getLogger(__name__)

# HAFAS ``prodCtx.catOut`` category label (whitespace-trimmed, upper-cased) ->
# unified transport type. Built on the shared Scotty category map, plus the
# mgate-specific labels seen live (Metro, single-letter bus/tram codes, …).
CATOUT_MAPPING: Dict[str, str] = {
    **{k: v for k, v in DEFAULT_CATEGORY_MAPPING.items()},
    "METRO": "subway",
    "TRAIN": "train",
    "COMMUTER": "train",
    "INTERCITY": "train",
    "DART": "train",     # Dublin commuter rail
    "LUAS": "tram",      # Dublin tram
    "B": "bus",          # TPG bus code
    "T": "tram",
    "FERRY": "ferry",
    "SHIP": "ferry",
    "BOAT": "ferry",
    # German regional networks (VMT, INSA, …)
    "STRAB": "tram",
    "STRB": "tram",
    "ZUG": "train",
    "DRE": "train",
}

# Substring fallbacks scanned against the lower-cased category when there is no
# exact match (order matters: more specific first).
_CATOUT_STEMS = [
    ("tram", "tram"),
    ("straßenbahn", "tram"),
    ("stadtbahn", "tram"),
    ("metro", "subway"),
    ("subway", "subway"),
    ("underground", "subway"),
    ("bus", "bus"),
    ("ferry", "ferry"),
    ("ship", "ferry"),
    ("boat", "ferry"),
    ("cable", "tram"),
    ("gondola", "tram"),
    ("funicular", "train"),
    ("train", "train"),
    ("rail", "train"),
    ("zug", "train"),
    ("regional", "train"),
    ("s-bahn", "train"),
]


def _hafas_datetime(
    date_str: str, time_str: str, tz: Union[ZoneInfo, Any]
) -> Optional[datetime]:
    """Combine a HAFAS ``YYYYMMDD`` date and ``[dd]HHMMSS`` time.

    Times may carry a leading day-offset (e.g. ``"01003000"`` = +1 day 00:30).
    """
    if not date_str or not time_str:
        return None
    try:
        offset_days = 0
        if len(time_str) > 6:
            offset_days = int(time_str[:-6])
            time_str = time_str[-6:]
        time_str = time_str.zfill(6)
        dt = datetime(
            int(date_str[0:4]), int(date_str[4:6]), int(date_str[6:8]),
            int(time_str[0:2]), int(time_str[2:4]), int(time_str[4:6]),
            tzinfo=tz,
        )
        return dt + timedelta(days=offset_days)
    except (ValueError, TypeError):
        return None


def _duration_minutes(dur: str) -> int:
    """Convert a HAFAS ``[dd]HHMMSS`` duration into minutes."""
    if not dur or not dur.isdigit():
        return 0
    days = int(dur[:-6]) if len(dur) > 6 else 0
    dur = dur[-6:].zfill(6)
    return days * 1440 + int(dur[0:2]) * 60 + int(dur[2:4])


def _platform(stb: Dict[str, Any], realtime: bool) -> str:
    """Extract a platform from a StationBoard stop, preferring realtime.

    Handles both the legacy string form (``dPlatfR``/``dPlatfS``) and the
    structured form (``dPltfR``/``dPltfS`` -> ``{"txt": …}``).
    """
    for key in (("dPlatfR", "dPltfR") if realtime else ()) + ("dPlatfS", "dPltfS"):
        val = stb.get(key)
        if isinstance(val, dict):
            txt = val.get("txt")
            if txt:
                return str(txt)
        elif val:
            return str(val)
    return ""


class HafasMgateBaseProvider(BaseProvider):
    """Base class for modern HAFAS ``mgate.exe`` providers."""

    mgate_endpoint: str = ""
    mgate_auth: Dict[str, Any] = {}
    mgate_client: Dict[str, Any] = {}
    mgate_ver: str = "1.18"
    mgate_lang: str = "en"
    timezone: str = "Europe/Berlin"
    # Optional request signing (unset for every endpoint we ship).
    checksum_salt: Optional[str] = None
    mic_mac_salt: Optional[str] = None

    def get_timezone(self) -> str:
        return self.timezone

    def get_catout_mapping(self) -> Dict[str, str]:
        return CATOUT_MAPPING

    async def _mgate_request(self, svc_req: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Call the mgate gateway and return the service result payload.

        Raises the library's API exceptions on transport failures, and
        ApiResponseError when mgate reports a protocol-level error.
        """
        body = {
            "lang": self.mgate_lang,
            "svcReqL": [svc_req],
            "client": self.mgate_client,
            "ver": self.mgate_ver,
            "auth": self.mgate_auth,
            "formatted": False,
        }
        raw = json.dumps(body, separators=(",", ":")).encode("utf-8")
        url = self.mgate_endpoint + self._signature_query(raw)

        data = await self._request(
            "post",
            url,
            data=raw,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            timeout=15,
        )

        if not isinstance(data, dict):
            raise ApiResponseError(f"{self.provider_name}: mgate returned {type(data).__name__} instead of an object")

        if data.get("err") and data.get("err") != "OK":
            raise ApiResponseError(
                f"{self.provider_name}: mgate error {data.get('err')} {data.get('errTxt', '')}".strip()
            )
        svc = (data.get("svcResL") or [{}])[0]
        if svc.get("err") and svc.get("err") != "OK":
            raise ApiResponseError(f"{self.provider_name}: mgate service error {svc.get('err')}")
        return svc.get("res")

    def _signature_query(self, raw: bytes) -> str:
        if self.checksum_salt:
            checksum = hashlib.md5(raw + self.checksum_salt.encode("utf-8")).hexdigest()
            return f"?checksum={checksum}"
        if self.mic_mac_salt:
            mic = hashlib.md5(raw).hexdigest()
            mac = hashlib.md5(mic.encode("utf-8") + self.mic_mac_salt.encode("utf-8")).hexdigest()
            return f"?mic={mic}&mac={mac}"
        return ""

    async def search_stops(self, search_term: str) -> List[Dict[str, Any]]:
        svc = {
            "meth": "LocMatch",
            "req": {"input": {"field": "S", "loc": {"type": "S", "name": search_term + "?"}, "maxLoc": 12}},
        }
        res = await self._mgate_request(svc)
        if not res:
            return []
        return self._stops_from_locmatch(res)

    @staticmethod
    def _stops_from_locmatch(res: Dict[str, Any]) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []
        for loc in res.get("match", {}).get("locL", []):
            if not isinstance(loc, dict):
                continue
            stop_id = loc.get("lid") or (str(loc["extId"]) if loc.get("extId") else "")
            name = loc.get("name", "")
            if not stop_id or not name:
                continue
            results.append({"id": stop_id, "name": name, "place": "", "area_type": "stop"})
        return results

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

        stb_loc = {"lid": station_id} if "@" in station_id or "=" in station_id else {"type": "S", "extId": station_id}
        svc = {
            "meth": "StationBoard",
            "req": {"type": "DEP", "stbLoc": stb_loc, "maxJny": departures_limit},
        }
        res = await self._mgate_request(svc)
        if res is None:
            return None

        prod_list = res.get("common", {}).get("prodL", [])
        events = []
        for jny in res.get("jnyL", []):
            if not isinstance(jny, dict):
                continue
            prod_x = jny.get("prodX")
            prod = prod_list[prod_x] if isinstance(prod_x, int) and 0 <= prod_x < len(prod_list) else {}
            events.append({**jny, "_prod": prod})
        return {"stopEvents": events}

    def parse_departure(
        self, stop: Dict[str, Any], tz: Union[ZoneInfo, Any], now: datetime
    ) -> Optional[UnifiedDeparture]:
        try:
            stb = stop.get("stbStop", {})
            date_str = stop.get("date", "")
            time_s = stb.get("dTimeS", "")
            time_r = stb.get("dTimeR", "")
            if not time_s and not time_r:
                return None

            planned = _hafas_datetime(date_str, time_s or time_r, tz)
            if not planned:
                return None
            is_realtime = bool(time_r)
            when = _hafas_datetime(date_str, time_r, tz) if is_realtime else planned
            if not when:
                when = planned

            delay_minutes = int((when - planned).total_seconds() / 60)

            prod = stop.get("_prod", {})
            line = prod.get("name") or prod.get("nameS") or ""
            transport_type = self._transport_type(prod)
            destination = stop.get("dirTxt") or "Unknown"
            platform = _platform(stb, is_realtime)

            minutes_until = max(0, int((when - now).total_seconds() / 60))

            cancelled = bool(jny_cancelled(stop, stb))
            notices = ["Cancelled"] if cancelled else None

            return UnifiedDeparture(
                line=line,
                destination=destination,
                departure_time=when.strftime("%H:%M"),
                planned_time=planned.strftime("%H:%M"),
                delay=delay_minutes,
                platform=platform,
                transportation_type=transport_type,
                is_realtime=is_realtime,
                minutes_until_departure=minutes_until,
                departure_time_obj=when,
                description=None,
                agency=None,
                notices=notices,
                planned_platform=None,
                platform_changed=False,
            )
        except Exception as e:  # noqa: BLE001
            _LOGGER.debug("Error parsing %s mgate departure: %s", self.provider_name, e)
            return None

    @staticmethod
    def _location(stop_id: str) -> Dict[str, Any]:
        """mgate location reference for a stop id from :meth:`search_stops`."""
        return {"lid": stop_id} if "@" in stop_id or "=" in stop_id else {"type": "S", "extId": stop_id}

    async def search_trips(
        self,
        origin_id: str,
        destination_id: str,
        departure_time: Optional[datetime] = None,
        results: int = 4,
    ) -> List[Dict[str, Any]]:
        """Plan journeys between two stops with the mgate ``TripSearch`` service.

        ``departure_time`` is converted to the network's time zone; without it
        the search starts now. Returns one dict per connection::

            {"duration_minutes": int, "transfers": int, "legs": [leg, ...]}

        Each leg carries ``mode`` ("transit" or "walk"), ``origin``,
        ``destination``, ``line``, ``product``, ``transport_type``, ``direction``,
        ``platform``, ``cancelled`` and the timezone-aware datetimes
        ``departure_planned`` / ``departure`` / ``arrival_planned`` / ``arrival``
        (the unprefixed ones are real-time when the network reports it).
        """
        tz = ZoneInfo(self.timezone)
        when = (departure_time or datetime.now(tz)).astimezone(tz)
        svc = {
            "meth": "TripSearch",
            "req": {
                "depLocL": [self._location(origin_id)],
                "arrLocL": [self._location(destination_id)],
                "outDate": when.strftime("%Y%m%d"),
                "outTime": when.strftime("%H%M%S"),
                "outFrwd": True,
                "numF": results,
                "getPasslist": False,
                "getPolyline": False,
            },
        }
        res = await self._mgate_request(svc)
        if not res:
            return []

        common = res.get("common") or {}
        locs = common.get("locL") or []
        prods = common.get("prodL") or []

        def pick(items: List[Dict[str, Any]], index: Any) -> Dict[str, Any]:
            return items[index] if isinstance(index, int) and 0 <= index < len(items) else {}

        trips = []
        for con in res.get("outConL") or []:
            date_str = con.get("date", "")
            legs = []
            for sec in con.get("secL") or []:
                dep = sec.get("dep") or {}
                arr = sec.get("arr") or {}
                jny = sec.get("jny") or {}
                is_transit = sec.get("type") == "JNY"
                prod = pick(prods, jny.get("prodX")) if is_transit else {}
                dep_planned = _hafas_datetime(date_str, dep.get("dTimeS", ""), tz)
                arr_planned = _hafas_datetime(date_str, arr.get("aTimeS", ""), tz)
                if not dep_planned or not arr_planned:
                    continue
                legs.append(
                    {
                        "mode": "transit" if is_transit else "walk",
                        "origin": pick(locs, dep.get("locX")).get("name", ""),
                        "destination": pick(locs, arr.get("locX")).get("name", ""),
                        "line": (prod.get("name") or prod.get("nameS") or "").strip(),
                        "product": (prod.get("prodCtx", {}).get("catOutL") or prod.get("prodCtx", {}).get("catOut") or "").strip(),
                        "transport_type": self._transport_type(prod) if is_transit else "walk",
                        "direction": jny.get("dirTxt", ""),
                        "platform": _platform(dep, bool(dep.get("dTimeR"))),
                        "cancelled": bool(jny.get("isCncl") or dep.get("dCncl") or arr.get("aCncl")),
                        "departure_planned": dep_planned,
                        "departure": _hafas_datetime(date_str, dep.get("dTimeR", ""), tz) or dep_planned,
                        "arrival_planned": arr_planned,
                        "arrival": _hafas_datetime(date_str, arr.get("aTimeR", ""), tz) or arr_planned,
                    }
                )
            if not any(leg["mode"] == "transit" for leg in legs):
                continue
            trips.append(
                {
                    "duration_minutes": _duration_minutes(con.get("dur", "")),
                    "transfers": int(con.get("chg") or 0),
                    "legs": legs,
                }
            )
        return trips

    def _transport_type(self, prod: Dict[str, Any]) -> str:
        ctx = prod.get("prodCtx", {})
        catout = (ctx.get("catOut") or "").strip()
        mapping = self.get_catout_mapping()
        key = catout.upper()
        if key in mapping:
            return mapping[key]
        # Fall back to the stems, first on the short and then on the long label
        # ("Strab" / "Straßenbahn").
        for label in (catout, (ctx.get("catOutL") or "").strip()):
            low = label.lower()
            for stem, typ in _CATOUT_STEMS:
                if stem in low:
                    return typ
        return "unknown"


def jny_cancelled(jny: Dict[str, Any], stb: Dict[str, Any]) -> bool:
    return bool(jny.get("isCncl") or stb.get("dCncl") or jny.get("dCncl"))
