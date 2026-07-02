"""Tests for the National Rail (UK) offline station-snapshot fallback.

Overpass (OSM) is the source of truth; the bundled snapshot is only consulted
when Overpass is unreachable/rate-limited or returns nothing.
"""

import openpublictransport.providers.national_rail as nr
from openpublictransport.providers.national_rail import NationalRailProvider


class _Resp:
    def __init__(self, status, payload):
        self.status = status
        self._payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def json(self, content_type=None):
        return self._payload


class _Session:
    """Fake session. mode='ok' returns a station; mode='down' simulates 429."""

    def __init__(self, mode):
        self.mode = mode
        self.calls = 0

    def post(self, url, data=None, headers=None, timeout=None):
        self.calls += 1
        if self.mode == "down":
            return _Resp(429, None)
        payload = {
            "elements": [
                {"tags": {"railway": "station", "ref:crs": "WIN",
                          "name": "Winchester Overpass", "operator": "SWR"}}
            ]
        }
        return _Resp(200, payload)


def test_snapshot_loads_and_has_known_stations():
    stations = nr._load_static_stations()
    assert len(stations) > 2000
    by_crs = {s["crs"]: s for s in stations}
    assert by_crs["WIN"]["name"] == "Winchester"
    assert by_crs["RDG"]["name"] == "Reading"


def test_static_search_by_crs_code():
    provider = NationalRailProvider(_Session("down"))
    results = provider._search_static("rdg")
    assert results == [
        {"id": "RDG", "name": "Reading", "place": "Network Rail", "area_type": "stop"}
    ]


def test_static_search_by_name():
    provider = NationalRailProvider(_Session("down"))
    results = provider._search_static("winchester")
    assert any(r["id"] == "WIN" and r["name"] == "Winchester" for r in results)


async def test_overpass_success_does_not_use_fallback():
    session = _Session("ok")
    provider = NationalRailProvider(session)
    results = await provider.search_stops("WIN")
    # Result comes from Overpass (note the distinct name), not the snapshot.
    assert results[0]["name"] == "Winchester Overpass"


async def test_falls_back_to_snapshot_when_overpass_down():
    session = _Session("down")
    provider = NationalRailProvider(session)
    results = await provider.search_stops("WIN")
    assert session.calls == 1  # Overpass was attempted
    assert results[0]["id"] == "WIN"
    assert results[0]["name"] == "Winchester"  # snapshot name, not the Overpass one


async def test_empty_term_returns_nothing_without_calling_overpass():
    session = _Session("ok")
    provider = NationalRailProvider(session)
    assert await provider.search_stops("   ") == []
    assert session.calls == 0
