"""HVV via the official Geofox Thin Interface (GTI).

The `hvv` provider talks to the public EFA endpoint. This one uses HOCHBAHN's
official API on behalf of the HVV, which carries real-time delays, cancellations
and platform changes that EFA does not expose.

Credentials are free but must be requested by email from api@hochbahn.de; the
API is documented at https://gti.geofox.de/html/GTIHandbuch_p.html.

Requested for https://github.com/NerdySoftPaw/openpublictransport/issues/61.
"""

import base64
import hashlib
import hmac
import json
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Union
from zoneinfo import ZoneInfo

import aiohttp

from ..const import PROVIDER_HVV_GTI
from ..exceptions import AuthenticationError
from ..models import UnifiedDeparture
from .base import BaseProvider

_LOGGER = logging.getLogger(__name__)

GTI_BASE_URL = "https://gti.geofox.de/gti/public"

# The API version the request bodies below are written against. The server
# tailors its response to this number, so it is pinned rather than left to the
# server default (which is the long-obsolete version 1).
GTI_API_VERSION = 63

# A line's type carries a granular value (`shortInfo`, and sometimes a granular
# `simpleType`) plus a coarse `simpleType`. The granular one is authoritative;
# the coarse one flattens U-Bahn, S-Bahn and AKN all into TRAIN, so it is only
# consulted after the line name has had a chance to disambiguate.
_GRANULAR_TYPE_MAP = {
    "UBAHN": "subway",
    "U": "subway",
    "U-BAHN": "subway",
    "SBAHN": "train",
    "S": "train",
    "S-BAHN": "train",
    "AKN": "train",
    "A": "train",
    "RBAHN": "train",
    "R": "train",
    "FERNBAHN": "train",
    "ZUG": "train",
    "STADTBUS": "bus",
    "METROBUS": "bus",
    "SCHNELLBUS": "bus",
    "NACHTBUS": "bus",
    "XPRESSBUS": "bus",
    "EILBUS": "bus",
    "FAEHRE": "ferry",
    "FÄHRE": "ferry",
    "AST": "taxi",
}

_COARSE_TYPE_MAP = {
    "BUS": "bus",
    "TRAIN": "train",
    "SHIP": "ferry",
}


def sign_request(password: str, body: bytes) -> str:
    """Return the geofox-auth-signature for a request body.

    HMAC-SHA1 over the UTF-8 request body, keyed with the password, Base64
    encoded — GTI handbook §3 "Authentifikation".
    """
    digest = hmac.new(password.encode("utf-8"), body, hashlib.sha1).digest()
    return base64.b64encode(digest).decode("ascii")


def _transport_type(line: Dict[str, Any]) -> str:
    """Map a GTI line to a unified transportation type."""
    line_type = line.get("type") if isinstance(line.get("type"), dict) else {}
    short_info = str(line_type.get("shortInfo") or "").strip().upper()
    simple_type = str(line_type.get("simpleType") or "").strip().upper()

    for value in (short_info, simple_type):
        if value in _GRANULAR_TYPE_MAP:
            return _GRANULAR_TYPE_MAP[value]

    # Only the coarse type is left, which lumps U-Bahn, S-Bahn and AKN together
    # under TRAIN. Hamburg's line names are unambiguous, so use them first.
    name = str(line.get("name") or "").strip().upper()
    if len(name) > 1 and name[1].isdigit():
        if name[0] == "U":
            return "subway"
        if name[0] in ("S", "A", "R"):
            return "train"

    if simple_type in _COARSE_TYPE_MAP:
        return _COARSE_TYPE_MAP[simple_type]

    _LOGGER.debug("HVV GTI: unmapped line type %s for line %r", line_type, line.get("name"))
    return "unknown"


class HVVGTIProvider(BaseProvider):
    """HVV via the official Geofox GTI API (real-time)."""

    @property
    def provider_id(self) -> str:
        return PROVIDER_HVV_GTI

    @property
    def provider_name(self) -> str:
        return "HVV Geofox GTI (Hamburg)"

    @property
    def requires_api_key(self) -> bool:
        return True

    def get_timezone(self) -> str:
        return "Europe/Berlin"

    # ── transport ────────────────────────────────────────────────────────────

    async def _post(self, method: str, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Call one GTI method, signing the exact bytes that are sent.

        The signature covers the serialized body, so the body is serialized
        once and handed to aiohttp as raw bytes — letting aiohttp re-serialize
        it via ``json=`` could change the bytes and invalidate the signature.
        """
        if not self.api_key or not self.api_key_secondary:
            _LOGGER.error("%s: username and password are required", self.provider_name)
            raise AuthenticationError(f"{self.provider_name}: username and password are required")

        body = json.dumps(
            {"version": GTI_API_VERSION, "language": "de", **payload},
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")

        headers = {
            "geofox-auth-type": "HmacSHA1",
            "geofox-auth-user": self.api_key,
            "geofox-auth-signature": sign_request(self.api_key_secondary, body),
            "Content-Type": "application/json;charset=UTF-8",
            "Accept": "application/json",
            "X-Platform": "web",
        }

        url = f"{GTI_BASE_URL}/{method}"
        try:
            async with self.session.post(
                url, data=body, headers=headers, timeout=aiohttp.ClientTimeout(total=15)
            ) as response:
                if response.status in (401, 403):
                    raise AuthenticationError(
                        f"{self.provider_name}: authentication failed (HTTP {response.status}) — "
                        "check the GTI username and password"
                    )
                if response.status != 200:
                    _LOGGER.warning("%s: %s → HTTP %s", self.provider_name, method, response.status)
                    return None

                data = await response.json(content_type=None)
        except AuthenticationError:
            raise
        except aiohttp.ClientError as exc:
            _LOGGER.warning("%s: %s request failed: %s", self.provider_name, method, exc)
            return None
        except Exception as exc:
            _LOGGER.warning("%s: %s error: %s", self.provider_name, method, exc)
            return None

        if not isinstance(data, dict):
            _LOGGER.warning("%s: %s returned %s, expected an object", self.provider_name, method, type(data))
            return None

        return_code = data.get("returnCode")
        if return_code != "OK":
            # GTI signals a bad key with a return code rather than an HTTP status.
            if return_code in ("ERROR_COMM", "ERROR_TEXT") and "auth" in str(data.get("errorDevInfo", "")).lower():
                raise AuthenticationError(f"{self.provider_name}: {data.get('errorText') or return_code}")
            _LOGGER.warning(
                "%s: %s returned %s — %s (%s)",
                self.provider_name,
                method,
                return_code,
                data.get("errorText") or "no message",
                data.get("errorDevInfo") or "",
            )
            return None

        return data

    # ── stop search ──────────────────────────────────────────────────────────

    async def search_stops(self, search_term: str) -> List[Dict[str, Any]]:
        """Search stations via the checkName method."""
        data = await self._post(
            "checkName",
            {
                "theName": {"name": search_term, "type": "STATION"},
                "maxList": 25,
                "coordinateType": "EPSG_4326",
            },
        )
        if not data:
            return []

        stops = []
        for result in data.get("results") or []:
            if not isinstance(result, dict) or result.get("type") != "STATION" or not result.get("id"):
                continue
            stops.append(
                {
                    "id": result["id"],
                    "name": result.get("name", ""),
                    "place": result.get("city", ""),
                    "area_type": "stop",
                }
            )
        return stops

    # ── departures ───────────────────────────────────────────────────────────

    async def fetch_departures(
        self,
        station_id: Optional[str],
        place_dm: str,
        name_dm: str,
        departures_limit: int,
    ) -> Optional[Dict[str, Any]]:
        """Fetch a departure board via the departureList method."""
        if not station_id:
            _LOGGER.warning("%s: a station ID is required", self.provider_name)
            return None

        now = datetime.now(ZoneInfo(self.get_timezone()))
        data = await self._post(
            "departureList",
            {
                "station": {"id": station_id, "type": "STATION"},
                "time": {"date": now.strftime("%d.%m.%Y"), "time": now.strftime("%H:%M")},
                "maxList": max(departures_limit, 5),
                "maxTimeOffset": 200,
                "useRealtime": True,
                "returnFilters": False,
                "coordinateType": "EPSG_4326",
            },
        )
        if not data:
            return None

        # Departures are offsets from the board's own reference time, not
        # absolute timestamps, so it has to be resolved here while we still
        # have the response that produced them.
        reference = self._reference_time(data.get("time"), now)

        stop_events = []
        for departure in data.get("departures") or []:
            event = self._to_stop_event(departure, reference)
            if event:
                stop_events.append(event)

        stop_events.sort(key=lambda event: event["estimated"])
        return {"stopEvents": stop_events[:departures_limit]}

    def _reference_time(self, gti_time: Any, fallback: datetime) -> datetime:
        """Parse the response's GTITime, falling back to the request time."""
        if isinstance(gti_time, dict) and gti_time.get("date") and gti_time.get("time"):
            try:
                return datetime.strptime(
                    f"{gti_time['date']} {gti_time['time']}", "%d.%m.%Y %H:%M"
                ).replace(tzinfo=ZoneInfo(self.get_timezone()))
            except ValueError:
                _LOGGER.debug("%s: unparseable reference time %s", self.provider_name, gti_time)
        return fallback

    def _to_stop_event(self, departure: Any, reference: datetime) -> Optional[Dict[str, Any]]:
        """Normalise one GTI departure into this provider's stop-event shape."""
        if not isinstance(departure, dict):
            return None

        line = departure.get("line") if isinstance(departure.get("line"), dict) else {}
        time_offset = departure.get("timeOffset")
        if not isinstance(time_offset, int):
            _LOGGER.debug("%s: departure without a timeOffset: %s", self.provider_name, departure)
            return None

        # planned = board reference + timeOffset; the real-time deviation is a
        # separate value in seconds on top (GTI handbook §2.4.2).
        delay_seconds = departure.get("delay") if isinstance(departure.get("delay"), int) else 0
        planned = reference + timedelta(minutes=time_offset)
        estimated = planned + timedelta(seconds=delay_seconds)

        platform = departure.get("realtimePlatform") or departure.get("platform") or ""
        planned_platform = departure.get("platform") or ""

        notices = []
        for attribute in departure.get("attributes") or []:
            if isinstance(attribute, dict):
                text = attribute.get("value") or attribute.get("title") or ""
                if text:
                    notices.append(str(text).strip())
        if departure.get("cancelled"):
            notices.append("Fahrt fällt aus")
        if departure.get("extra"):
            notices.append("Verstärkerfahrt")

        return {
            "line": str(line.get("name") or ""),
            "destination": str(line.get("direction") or "Unknown"),
            "transportType": _transport_type(line),
            "planned": planned,
            "estimated": estimated,
            "delaySeconds": delay_seconds,
            "isRealtime": departure.get("delay") is not None,
            "platform": str(platform),
            "plannedPlatform": str(planned_platform),
            "cancelled": bool(departure.get("cancelled")),
            "notices": notices or None,
        }

    def parse_departure(
        self, stop: Dict[str, Any], tz: Union[ZoneInfo, Any], now: datetime
    ) -> Optional[UnifiedDeparture]:
        """Build a UnifiedDeparture from a stop event produced above."""
        try:
            planned = stop["planned"].astimezone(tz)
            estimated = stop["estimated"].astimezone(tz)

            platform = stop.get("platform") or ""
            planned_platform = stop.get("plannedPlatform") or ""
            platform_changed = bool(platform and planned_platform and platform != planned_platform)

            return UnifiedDeparture(
                line=stop.get("line", ""),
                destination=stop.get("destination", "Unknown"),
                departure_time=estimated.strftime("%H:%M"),
                planned_time=planned.strftime("%H:%M"),
                delay=int(stop.get("delaySeconds", 0) / 60),
                platform=platform,
                transportation_type=stop.get("transportType", "unknown"),
                is_realtime=bool(stop.get("isRealtime")),
                minutes_until_departure=max(0, int((estimated - now).total_seconds() / 60)),
                departure_time_obj=estimated,
                notices=stop.get("notices"),
                planned_platform=planned_platform if platform_changed else None,
                platform_changed=platform_changed,
            )
        except Exception as exc:
            _LOGGER.debug("%s parse_departure error: %s", self.provider_name, exc)
            return None
