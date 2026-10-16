"""Digitransit (Finland) provider — OpenTripPlanner 2 GraphQL.

Requires a free subscription key from https://portal-api.digitransit.fi/,
sent as the ``digitransit-subscription-key`` header.

Default router is ``finland`` (all of Finland, incl. HSL Helsinki, Tampere,
Turku, Oulu, VR long-distance trains). Regional routers can be selected via
``custom_url``: either the full GraphQL URL or one of the shorthands
``"finland"``, ``"hsl"`` (Helsinki region) and ``"waltti"`` (Waltti cities).

Stop ids are GTFS ids (``HSL:1020453``); stops that share a parent station or
lie close together with the same name are combined with ``|`` like the other
OTP2 providers.
"""

import logging
from typing import Any, Dict, List, Optional

from .otp import OTPProvider, _smart_title
from ..const import PROVIDER_DIGITRANSIT

_LOGGER = logging.getLogger(__name__)


ROUTER_URLS: Dict[str, str] = {
    "finland": "https://api.digitransit.fi/routing/v2/finland/gtfs/v1",
    "hsl": "https://api.digitransit.fi/routing/v2/hsl/gtfs/v1",
    "waltti": "https://api.digitransit.fi/routing/v2/waltti/gtfs/v1",
}

MODE_MAPPING: Dict[str, str] = {
    "BUS": "bus",
    "COACH": "bus",
    "TROLLEYBUS": "bus",
    "RAIL": "train",
    "TRAM": "tram",
    "SUBWAY": "subway",
    "FERRY": "ferry",
    "FUNICULAR": "train",
    "GONDOLA": "tram",
    "CABLE_CAR": "tram",
}


class DigitransitProvider(OTPProvider):
    """Digitransit Finland (HSL, Waltti, VR …)."""

    otp_base_url: str = ROUTER_URLS["finland"]
    geocode_countries: str = "fi"

    @property
    def provider_id(self) -> str:
        return PROVIDER_DIGITRANSIT

    @property
    def provider_name(self) -> str:
        return "Digitransit (Finnland)"

    @property
    def requires_api_key(self) -> bool:
        return True

    def get_timezone(self) -> str:
        return "Europe/Helsinki"

    def get_mode_mapping(self) -> Dict[str, str]:
        return MODE_MAPPING

    @property
    def graphql_url(self) -> str:
        custom = (self.custom_url or "").strip()
        if custom:
            return ROUTER_URLS.get(custom.lower(), custom.rstrip("/"))
        return self.otp_base_url

    def _auth_headers(self) -> Dict[str, str]:
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if self.api_key:
            headers["digitransit-subscription-key"] = self.api_key
        return headers

    async def _graphql(self, query: str) -> Optional[Dict[str, Any]]:
        # Digitransit's endpoint *is* the GraphQL URL (no /index/graphql suffix).
        return await self._request(
            "post",
            self.graphql_url,
            json={"query": query},
            headers=self._auth_headers(),
            timeout=15,
        )

    async def search_stops(self, search_term: str) -> List[Dict[str, Any]]:
        """Search by stop name; no German city-prefix / Nominatim fallbacks."""
        term = search_term.strip()
        if not term:
            return []
        smart = _smart_title(term)
        for candidate in dict.fromkeys([term, smart]):
            raw = await self._search_one(candidate)
            if raw:
                return self._group_by_name(raw)[:20]
        return []
