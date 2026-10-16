"""CTA provider (Chicago Transit Authority) — real-time 'L' trains and buses.

Departures come from CTA's own real-time APIs, which use separate keys:

* Train Tracker Arrivals API (``api_key_secondary``):
  https://www.transitchicago.com/developers/ttdocs/
* Bus Tracker API v3 (``api_key``):
  https://www.transitchicago.com/developers/bustracker/

Stop search needs no key: it uses the Transitous geocoder (which carries
CTA's GTFS stop ids) and keeps only CTA stops. Per CTA's docs, stop ids
0–29999 are bus stops, 30000–39999 'L' platforms and 40000–49999 'L'
parent stations. Ids are encoded as ``train:<id>`` or ``bus:<id>``.

Both CTA APIs report errors (including an invalid key) with HTTP 200 and an
error object in the body, so the payload is inspected here.
"""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple, Union
from urllib.parse import quote
from zoneinfo import ZoneInfo

from ..exceptions import ApiError, ApiResponseError, AuthenticationError
from ..models import UnifiedDeparture
from .base import BaseProvider
from .transitous import API_BASE as TRANSITOUS_API_BASE
from .transitous import USER_AGENT
from ..const import PROVIDER_CTA_US

_LOGGER = logging.getLogger(__name__)

PROVIDER_ID = PROVIDER_CTA_US
PROVIDER_NAME = "CTA (Chicago)"
TIMEZONE = "America/Chicago"

TRAIN_API_URL = "https://lapi.transitchicago.com/api/1.0/ttarrivals.aspx"
BUS_API_URL = "https://www.ctabustracker.com/bustime/api/v3/getpredictions"

# Transitous feed prefix for CTA GTFS stop ids.
TRANSITOUS_PREFIX = "us-il-CTA_"
# Downtown Chicago, used to bias the geocoder towards CTA stops.
CHICAGO_PLACE = "41.8781,-87.6298"

KIND_TRAIN = "train"
KIND_BUS = "bus"

# Train Tracker route codes → (display name, background colour, text colour).
TRAIN_ROUTES: Dict[str, Tuple[str, str, str]] = {
    "Red": ("Red Line", "#C60C30", "#FFFFFF"),
    "Blue": ("Blue Line", "#00A1DE", "#FFFFFF"),
    "Brn": ("Brown Line", "#62361B", "#FFFFFF"),
    "G": ("Green Line", "#009B3A", "#FFFFFF"),
    "Org": ("Orange Line", "#F9461C", "#FFFFFF"),
    "P": ("Purple Line", "#522398", "#FFFFFF"),
    "Pink": ("Pink Line", "#E27EA6", "#FFFFFF"),
    "Y": ("Yellow Line", "#F9E300", "#000000"),
}

TRANSPORT_TYPE_MAPPING = {KIND_TRAIN: "subway", KIND_BUS: "bus"}

# Train Tracker errCd values (docs: "Error Codes").
TRAIN_AUTH_ERRORS = {"101"}
TRAIN_QUOTA_ERRORS = {"102"}

# Bus Tracker dyn values (docs: "Dynamic Action Types").
BUS_DYN_CANCELLED = {1, 18}
BUS_DYN_EXPRESSED = {4}
BUS_DYN_HIDDEN = {16, 17}  # invalidated trips: "should not be shown to the public"

# Bus Tracker error messages (docs: "Error codes"), matched case-insensitively.
BUS_AUTH_ERRORS = ("invalid api access key", "no api access key")
BUS_QUOTA_ERRORS = ("transaction limit",)
BUS_EMPTY_ERRORS = ("no arrival times", "no service scheduled", "no data found")

MAX_TRAIN_RESULTS = 50
MAX_BUS_RESULTS = 30

_SOURCE_TZ = ZoneInfo(TIMEZONE)


def stop_kind(stop_id: str) -> str:
    """Return ``train`` or ``bus`` for a bare CTA stop id, using CTA's id ranges."""
    try:
        number = int(stop_id)
    except (TypeError, ValueError):
        return KIND_BUS
    return KIND_TRAIN if 30000 <= number <= 49999 else KIND_BUS


def encode_stop_id(stop_id: str) -> str:
    """Return the ``train:<id>`` / ``bus:<id>`` form used as station_id."""
    return f"{stop_kind(stop_id)}:{stop_id}"


def decode_stop_id(station_id: str) -> Tuple[str, str]:
    """Split a station_id into (kind, bare id). Bare and Transitous ids are accepted."""
    value = str(station_id).strip()
    if value.startswith(TRANSITOUS_PREFIX):
        value = value[len(TRANSITOUS_PREFIX):]
    kind, sep, rest = value.partition(":")
    if sep and kind in (KIND_TRAIN, KIND_BUS) and rest:
        return kind, rest
    return stop_kind(value), value


def _flag(value: Any) -> bool:
    """CTA encodes booleans as "0"/"1", "true"/"false" or real booleans."""
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true")


def _parse_train_time(value: Any) -> Optional[datetime]:
    """Parse arrT: the JSON feed uses ISO, the docs table "yyyyMMdd HH:mm:ss"."""
    if not value or not isinstance(value, str):
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y%m%d %H:%M:%S", "%Y%m%d %H:%M"):
        try:
            return datetime.strptime(value.strip(), fmt).replace(tzinfo=_SOURCE_TZ)
        except ValueError:
            continue
    return None


def _parse_bus_time(value: Any) -> Optional[datetime]:
    """Parse prdtm ("yyyyMMdd HH:mm", optionally with seconds), Chicago local time."""
    if not value or not isinstance(value, str):
        return None
    for fmt in ("%Y%m%d %H:%M", "%Y%m%d %H:%M:%S"):
        try:
            return datetime.strptime(value.strip(), fmt).replace(tzinfo=_SOURCE_TZ)
        except ValueError:
            continue
    return None


class CTAProvider(BaseProvider):
    """Chicago Transit Authority: Train Tracker ('L') and Bus Tracker predictions.

    ``api_key`` is the Bus Tracker key, ``api_key_secondary`` the Train
    Tracker key. Each is only needed for the matching stop type.
    """

    @property
    def provider_id(self) -> str:
        return PROVIDER_ID

    @property
    def provider_name(self) -> str:
        return PROVIDER_NAME

    @property
    def requires_api_key(self) -> bool:
        return True

    def get_timezone(self) -> str:
        return TIMEZONE

    def get_transport_type_mapping(self) -> Dict[Any, str]:
        return dict(TRANSPORT_TYPE_MAPPING)

    # ------------------------------------------------------------------ search

    async def _geocode(self, text: str) -> List[Dict[str, Any]]:
        url = (
            f"{TRANSITOUS_API_BASE}/v1/geocode?text={quote(text, safe='')}"
            f"&type=STOP&place={CHICAGO_PLACE}&placeBias=5"
        )
        data = await self._request("get", url, headers={"User-Agent": USER_AGENT}, timeout=10)
        if not isinstance(data, list):
            raise ApiResponseError(f"{PROVIDER_NAME}: geocoder returned {type(data).__name__} instead of a list")
        return [loc for loc in data if isinstance(loc, dict)]

    @staticmethod
    def _cta_stops(locations: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []
        seen = set()
        for loc in locations:
            raw_id = str(loc.get("id") or "")
            if not raw_id.startswith(TRANSITOUS_PREFIX):
                continue
            bare = raw_id[len(TRANSITOUS_PREFIX):]
            if not bare or bare in seen:
                continue
            seen.add(bare)
            kind = stop_kind(bare)
            results.append(
                {
                    "id": f"{kind}:{bare}",
                    "name": loc.get("name") or bare,
                    "place": "Chicago",
                    "area_type": "stop",
                    "transport_modes": [TRANSPORT_TYPE_MAPPING[kind]],
                }
            )
        return results

    async def search_stops(self, search_term: str) -> List[Dict[str, Any]]:
        term = (search_term or "").strip()
        if not term:
            return []
        results = self._cta_stops(await self._geocode(term))
        if not results and "chicago" not in term.lower():
            # A short or ambiguous term can be crowded out by other cities.
            results = self._cta_stops(await self._geocode(f"{term}, Chicago"))
        return results

    # -------------------------------------------------------------- departures

    def _require_key(self, kind: str) -> str:
        key = self.api_key_secondary if kind == KIND_TRAIN else self.api_key
        if not key:
            if kind == KIND_TRAIN:
                message = f"{PROVIDER_NAME}: a Train Tracker key is required for 'L' stations"
            else:
                message = f"{PROVIDER_NAME}: a Bus Tracker key is required for bus stops"
            raise AuthenticationError(PROVIDER_NAME, 401, message=message)
        return key

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

        kind, stop_id = decode_stop_id(station_id)
        key = self._require_key(kind)
        if kind == KIND_TRAIN:
            events = await self._fetch_train(key, stop_id, departures_limit)
        else:
            events = await self._fetch_bus(key, stop_id, departures_limit)
        return {"stopEvents": events[: max(0, departures_limit)]}

    async def _fetch_train(self, key: str, stop_id: str, limit: int) -> List[Dict[str, Any]]:
        # Parent stations (4xxxx) are queried by mapid, platforms (3xxxx) by stpid.
        id_param = "mapid" if stop_id.startswith("4") else "stpid"
        params = {
            "key": key,
            id_param: stop_id,
            "max": max(1, min(limit, MAX_TRAIN_RESULTS)),
            "outputType": "JSON",
        }
        data = await self._request("get", TRAIN_API_URL, params=params, timeout=15)
        root = data.get("ctatt") if isinstance(data, dict) else None
        if not isinstance(root, dict):
            raise ApiResponseError(f"{PROVIDER_NAME}: unexpected Train Tracker payload")

        code = str(root.get("errCd") or "0").strip()
        if code != "0":
            message = f"{PROVIDER_NAME}: Train Tracker error {code} ({root.get('errNm') or 'unknown'})"
            if code in TRAIN_AUTH_ERRORS:
                raise AuthenticationError(PROVIDER_NAME, 401, message=message)
            if code in TRAIN_QUOTA_ERRORS:
                raise ApiError(PROVIDER_NAME, 429, message=message)
            raise ApiResponseError(message)

        etas = root.get("eta") or []
        if isinstance(etas, dict):
            etas = [etas]
        return [{**eta, "_cta": KIND_TRAIN} for eta in etas if isinstance(eta, dict)]

    async def _fetch_bus(self, key: str, stop_id: str, limit: int) -> List[Dict[str, Any]]:
        params = {
            "key": key,
            "stpid": stop_id,
            "format": "json",
            "top": max(1, min(limit, MAX_BUS_RESULTS)),
        }
        data = await self._request("get", BUS_API_URL, params=params, timeout=15)
        root = data.get("bustime-response") if isinstance(data, dict) else None
        if not isinstance(root, dict):
            raise ApiResponseError(f"{PROVIDER_NAME}: unexpected Bus Tracker payload")

        predictions = root.get("prd") or []
        if isinstance(predictions, dict):
            predictions = [predictions]
        errors = root.get("error") or []
        if isinstance(errors, dict):
            errors = [errors]

        if errors and not predictions:
            messages = [str(e.get("msg") or "") for e in errors if isinstance(e, dict)]
            lowered = " | ".join(messages).lower()
            message = f"{PROVIDER_NAME}: Bus Tracker error ({'; '.join(m for m in messages if m) or 'unknown'})"
            if any(token in lowered for token in BUS_AUTH_ERRORS):
                raise AuthenticationError(PROVIDER_NAME, 401, message=message)
            if any(token in lowered for token in BUS_QUOTA_ERRORS):
                raise ApiError(PROVIDER_NAME, 429, message=message)
            if any(token in lowered for token in BUS_EMPTY_ERRORS):
                return []
            raise ApiResponseError(message)

        events = []
        for prd in predictions:
            if not isinstance(prd, dict):
                continue
            try:
                if int(prd.get("dyn") or 0) in BUS_DYN_HIDDEN:
                    continue
            except (TypeError, ValueError):
                pass
            events.append({**prd, "_cta": KIND_BUS})
        return events

    # ----------------------------------------------------------------- parsing

    def parse_departure(
        self, stop: Dict[str, Any], tz: Union[ZoneInfo, Any], now: datetime
    ) -> Optional[UnifiedDeparture]:
        try:
            kind = stop.get("_cta") or (KIND_TRAIN if "arrT" in stop else KIND_BUS)
            if kind == KIND_TRAIN:
                return self._parse_train(stop, tz, now)
            return self._parse_bus(stop, tz, now)
        except Exception as e:  # noqa: BLE001
            _LOGGER.debug("Error parsing CTA departure: %s", e)
            return None

    def _parse_train(self, eta: Dict[str, Any], tz: Any, now: datetime) -> Optional[UnifiedDeparture]:
        arrival = _parse_train_time(eta.get("arrT"))
        if arrival is None:
            return None
        dep_local = arrival.astimezone(tz)

        route = str(eta.get("rt") or "")
        line_name, color, text_color = TRAIN_ROUTES.get(route, (route, None, None))

        notices: List[str] = []
        if _flag(eta.get("isDly")):
            notices.append("Delayed")

        return UnifiedDeparture(
            line=line_name,
            destination=eta.get("destNm") or "Unknown",
            departure_time=dep_local.strftime("%H:%M"),
            planned_time=dep_local.strftime("%H:%M"),
            delay=0,
            platform=None,
            transportation_type=TRANSPORT_TYPE_MAPPING[KIND_TRAIN],
            is_realtime=not _flag(eta.get("isSch")),
            minutes_until_departure=max(0, int((dep_local - now).total_seconds() / 60)),
            departure_time_obj=dep_local,
            description=eta.get("stpDe") or None,
            agency="CTA",
            notices=notices or None,
            line_color=color,
            line_text_color=text_color,
        )

    def _parse_bus(self, prd: Dict[str, Any], tz: Any, now: datetime) -> Optional[UnifiedDeparture]:
        predicted = _parse_bus_time(prd.get("prdtm"))
        if predicted is None:
            return None
        dep_local = predicted.astimezone(tz)

        try:
            dyn = int(prd.get("dyn") or 0)
        except (TypeError, ValueError):
            dyn = 0

        notices: List[str] = []
        if dyn in BUS_DYN_CANCELLED:
            notices.append("Cancelled")
        if _flag(prd.get("dly")) or str(prd.get("prdctdn") or "").upper() == "DLY":
            notices.append("Delayed")
        if dyn in BUS_DYN_EXPRESSED:
            notices.append("Drop-off only")

        countdown = str(prd.get("prdctdn") or "").strip()
        if countdown.upper() == "DUE":
            minutes_until = 0
        elif countdown.isdigit():
            minutes_until = int(countdown)
        else:
            minutes_until = max(0, int((dep_local - now).total_seconds() / 60))

        return UnifiedDeparture(
            line=str(prd.get("rtdd") or prd.get("rt") or ""),
            destination=prd.get("des") or "Unknown",
            departure_time=dep_local.strftime("%H:%M"),
            planned_time=dep_local.strftime("%H:%M"),
            delay=0,
            platform=None,
            transportation_type=TRANSPORT_TYPE_MAPPING[KIND_BUS],
            is_realtime=True,
            minutes_until_departure=minutes_until,
            departure_time_obj=dep_local,
            description=prd.get("rtdir") or None,
            agency="CTA",
            notices=notices or None,
        )
