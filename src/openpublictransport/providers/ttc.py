"""TTC (Toronto Transit Commission) provider via the NextBus / umo IQ feed.

Covers TTC buses and streetcars. The subway (Lines 1, 2, 4) is *not* part of
the NextBus feed, so subway departures are not available from this provider.

Transport type: streetcar routes 500–599 and the Blue Night streetcar routes
(301, 304, 305, 306, 310, 312) are ``tram``; everything else is ``bus``. The
300-series is mostly Blue Night *bus* routes, so it is not mapped wholesale.
"""

from .nextbus_base import NextBusBaseProvider
from ..const import PROVIDER_TTC_CA


#: Blue Night (300-series) routes operated with streetcars.
TTC_NIGHT_STREETCAR_ROUTES = frozenset({"301", "304", "305", "306", "310", "312"})


class TTCProvider(NextBusBaseProvider):
    """Toronto Transit Commission (buses and streetcars)."""

    agency = "ttc"
    timezone = "America/Toronto"
    place = "Toronto"

    @property
    def provider_id(self) -> str:
        return PROVIDER_TTC_CA

    @property
    def provider_name(self) -> str:
        return "TTC (Toronto)"

    def transport_type_for_route(self, route_tag: str) -> str:
        tag = str(route_tag or "").strip()
        if tag in TTC_NIGHT_STREETCAR_ROUTES:
            return "tram"
        digits = "".join(ch for ch in tag if ch.isdigit())
        if digits and 500 <= int(digits) <= 599:
            return "tram"
        return "bus"
