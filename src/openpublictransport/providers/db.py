"""Deutsche Bahn provider implementation.

Served through Transitous (MOTIS 2, api.transitous.org), which carries the
nationwide DELFI timetable with DB real-time data. No API key required.

The provider used to talk to the community proxy v6.db.transport.rest, which
has been unavailable since autumn 2026. Entries created back then store a
numeric station number (IBNR) as stop id; those are resolved once by name to
the matching Transitous stop.
"""

import logging
from typing import Any, Dict, List, Optional

from ..const import PROVIDER_DB
from .transitous import TransitousProvider

_LOGGER = logging.getLogger(__name__)


class DBProvider(TransitousProvider):
    """Deutsche Bahn (Germany-wide) via Transitous."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._legacy_ids: Dict[str, str] = {}

    @property
    def provider_id(self) -> str:
        return PROVIDER_DB

    @property
    def provider_name(self) -> str:
        return "DB (Deutsche Bahn)"

    def get_timezone(self) -> str:
        return "Europe/Berlin"

    async def search_stops(self, search_term: str) -> List[Dict[str, Any]]:
        """Transitous searches worldwide; keep the German stops."""
        stops = await super().search_stops(search_term)
        german = [stop for stop in stops if stop.get("country") in (None, "", "DE")]
        return german or stops

    async def fetch_departures(
        self,
        station_id: Optional[str],
        place_dm: str,
        name_dm: str,
        departures_limit: int,
    ) -> Optional[Dict[str, Any]]:
        if station_id and station_id.isdigit():
            station_id = await self._resolve_legacy_id(station_id, name_dm or place_dm)
        return await super().fetch_departures(station_id, place_dm, name_dm, departures_limit)

    async def _resolve_legacy_id(self, ibnr: str, name: str) -> Optional[str]:
        """Map a v6.db.transport.rest station number to a Transitous stop id."""
        if ibnr in self._legacy_ids:
            return self._legacy_ids[ibnr]
        if not name:
            _LOGGER.warning("DB: cannot resolve legacy station %s without a stop name", ibnr)
            return None
        stops = await self.search_stops(name)
        if not stops:
            _LOGGER.warning("DB: no Transitous stop found for legacy station %s (%s)", ibnr, name)
            return None
        self._legacy_ids[ibnr] = stops[0]["id"]
        _LOGGER.debug("DB: legacy station %s (%s) resolved to %s", ibnr, name, stops[0]["id"])
        return stops[0]["id"]
