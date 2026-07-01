"""Tests for the National Rail (UK) provider stop search.

Focus: a 3-letter CRS code (e.g. "WIN") must be looked up directly against the
OSM ``ref:crs`` tag, while a longer term is matched against station names.
Regression test for https://github.com/NerdySoftPaw/openpublictransport/issues/39
"""

import pytest

from openpublictransport.providers.national_rail import NationalRailProvider


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload
        self.status = 200

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def json(self, content_type=None):
        return self._payload


class _FakeSession:
    """Records the Overpass query and returns a canned payload."""

    def __init__(self, payload):
        self._payload = payload
        self.last_query = None

    def post(self, url, data=None, headers=None, timeout=None):
        # The Overpass query is sent as form data under the "data" key.
        self.last_query = (data or {}).get("data", "")
        return _FakeResponse(self._payload)


_PAYLOAD = {
    "elements": [
        {
            "type": "node",
            "tags": {
                "railway": "station",
                "ref:crs": "WIN",
                "name": "Winchester",
                "operator": "South Western Railway",
            },
        }
    ]
}


async def _search(term):
    session = _FakeSession(_PAYLOAD)
    provider = NationalRailProvider(session)
    results = await provider.search_stops(term)
    return results, session.last_query


async def test_crs_code_search_queries_ref_crs():
    """A 3-letter code searches ref:crs directly, not the name."""
    results, query = await _search("WIN")

    assert '["ref:crs"="WIN"]' in query
    assert '"name"~' not in query

    assert results == [
        {
            "id": "WIN",
            "name": "Winchester",
            "place": "South Western Railway",
            "area_type": "stop",
        }
    ]


async def test_lowercase_code_is_uppercased():
    """CRS codes are case-insensitive and normalised to uppercase."""
    _, query = await _search("win")
    assert '["ref:crs"="WIN"]' in query


async def test_name_search_queries_name_tags():
    """A longer term matches the name tags, not an exact CRS code."""
    _, query = await _search("Winchester")

    assert '"name"~"Winchester"' in query
    assert '["ref:crs"="' not in query
