"""EFA endpoints that mislabel their JSON payload (issue #79).

VGN answers ``outputFormat=RapidJSON`` requests with
``Content-Type: text/xml;;charset=utf-8``. aiohttp raises ``ContentTypeError``
for that unless the caller passes ``content_type=None``, so station search
returned "no results found" even though the body was valid JSON.

The fake response below mimics aiohttp: it raises unless the strict content
type check is disabled — a mock that accepts the kwarg either way cannot catch
this regression.
"""

import aiohttp
import pytest

from openpublictransport.providers.vgn import VGNProvider

STOPFINDER_PAYLOAD = {
    "locations": [
        {
            "id": "de:09564:704",
            "name": "Nürnberg, Rathenauplatz",
            "disassembledName": "Rathenauplatz, Nürnberg",
            "type": "stop",
        }
    ]
}

DM_PAYLOAD = {
    "stopEvents": [
        {
            "departureTimePlanned": "2026-08-08T10:00:00Z",
            "transportation": {"number": "U2", "product": {"class": 2, "name": "U-Bahn"}},
        }
    ]
}


class _XmlMimeResponse:
    """Response whose body is JSON but whose header claims text/xml."""

    def __init__(self, payload, status=200):
        self.status = status
        self._payload = payload

    async def json(self, content_type="application/json"):
        if content_type is not None:
            raise aiohttp.ContentTypeError(
                None,
                (),
                message=("Attempt to decode JSON with unexpected mimetype: text/xml;;charset=utf-8"),
            )
        return self._payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakeSession:
    def __init__(self, payload):
        self._payload = payload
        self.urls = []

    def get(self, url, headers=None, timeout=None):
        self.urls.append(url)
        return _XmlMimeResponse(self._payload)


@pytest.mark.asyncio
async def test_search_stops_accepts_xml_mimetype():
    session = _FakeSession(STOPFINDER_PAYLOAD)
    results = await VGNProvider(session).search_stops("Rathenauplatz, Nürnberg")

    assert [r["id"] for r in results] == ["de:09564:704"]
    assert results[0]["place"] == "Nürnberg"


@pytest.mark.asyncio
async def test_fetch_departures_accepts_xml_mimetype():
    session = _FakeSession(DM_PAYLOAD)
    data = await VGNProvider(session).fetch_departures("de:09564:704", "", "", 10)

    assert data is not None
    assert len(data["stopEvents"]) == 1
