"""Tests for the HVV Geofox GTI provider.

The official HOCHBAHN/HVV API. Payloads follow the examples in the GTI handbook
(https://gti.geofox.de/html/GTIHandbuch_p.html).
See https://github.com/NerdySoftPaw/openpublictransport/issues/61.
"""

import base64
import hashlib
import hmac
import json
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from openpublictransport.exceptions import AuthenticationError
from openpublictransport.providers import get_provider
from openpublictransport.providers.hvv_gti import HVVGTIProvider, sign_request

TZ = ZoneInfo("Europe/Berlin")
NOW = datetime(2026, 8, 1, 21, 28, tzinfo=TZ)

# From the handbook's departureList example, extended with the real-time fields.
DEPARTURE_LIST_RESPONSE = {
    "returnCode": "OK",
    "time": {"date": "01.08.2026", "time": "21:28"},
    "departures": [
        {
            "line": {
                "name": "476",
                "direction": "Ahrensburg, Auestieg",
                "type": {"simpleType": "BUS", "shortInfo": "Bus"},
                "id": "VHH:476_VHH",
            },
            "timeOffset": 20,
            "serviceId": 200023670,
            "platform": "5",
            "delay": 120,
            "extra": False,
            "cancelled": False,
            "realtimePlatform": "5",
        },
        {
            "line": {
                "name": "U1",
                "direction": "Norderstedt Mitte",
                "type": {"simpleType": "TRAIN", "shortInfo": "U"},
                "id": "HHA-U:U1_HHA-U",
            },
            "timeOffset": 4,
            "serviceId": 200023671,
            "platform": "2",
            "realtimePlatform": "3",
            "delay": 0,
            "extra": False,
            "cancelled": False,
        },
        {
            "line": {
                "name": "S1",
                "direction": "Wedel",
                "type": {"simpleType": "TRAIN", "shortInfo": "S"},
                "id": "DBAG-S:S1_DBAG-S",
            },
            "timeOffset": 9,
            "serviceId": 200023672,
            "platform": "3",
            "cancelled": True,
            "attributes": [{"title": "Hinweis", "value": "Bauarbeiten"}],
        },
    ],
    "serviceTypes": ["BUS", "TRAIN"],
}

CHECK_NAME_RESPONSE = {
    "returnCode": "OK",
    "results": [
        {
            "name": "Christuskirche",
            "city": "Hamburg",
            "combinedName": "Christuskirche",
            "id": "Master:84902",
            "type": "STATION",
            "coordinate": {"x": 9.93454, "y": 53.552405},
            "serviceTypes": ["bus", "u"],
            "hasStationInformation": True,
        },
        # Non-station suggestions must be filtered out
        {"name": "Christuskirche 1", "city": "Hamburg", "id": "Coord:9.9,53.5", "type": "ADDRESS"},
    ],
}


class _FakeResponse:
    def __init__(self, status, payload):
        self.status = status
        self._payload = payload

    async def json(self, content_type=None):
        return self._payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakeSession:
    """Records the exact bytes and headers of each POST."""

    def __init__(self, status=200, payload=None):
        self.status = status
        self.payload = payload if payload is not None else {"returnCode": "OK"}
        self.calls = []

    def post(self, url, data=None, headers=None, timeout=None):
        self.calls.append(SimpleNamespace(url=url, body=data, headers=headers))
        return _FakeResponse(self.status, self.payload)


def _provider(session) -> HVVGTIProvider:
    return HVVGTIProvider(session, api_key="testuser", api_key_secondary="s3cr3t")


# ── authentication ────────────────────────────────────────────────────────────


def test_sign_request_matches_hmac_sha1_base64():
    body = b'{"version":63}'
    expected = base64.b64encode(hmac.new(b"s3cr3t", body, hashlib.sha1).digest()).decode()

    assert sign_request("s3cr3t", body) == expected


async def test_signature_covers_the_exact_bytes_sent():
    """The body must be signed and transmitted as the same bytes.

    Re-serialising the payload (e.g. handing aiohttp `json=`) would change the
    bytes and the server would reject the signature.
    """
    session = _FakeSession(payload=CHECK_NAME_RESPONSE)
    await _provider(session).search_stops("Christuskirche")

    call = session.calls[0]
    assert isinstance(call.body, bytes)
    assert call.headers["geofox-auth-signature"] == sign_request("s3cr3t", call.body)
    assert call.headers["geofox-auth-type"] == "HmacSHA1"
    assert call.headers["geofox-auth-user"] == "testuser"


async def test_request_pins_the_api_version():
    session = _FakeSession(payload=CHECK_NAME_RESPONSE)
    await _provider(session).search_stops("Christuskirche")

    body = json.loads(session.calls[0].body.decode("utf-8"))
    assert body["version"] == 63
    assert body["language"] == "de"


async def test_missing_credentials_raise():
    provider = HVVGTIProvider(_FakeSession(), api_key="testuser", api_key_secondary=None)

    with pytest.raises(AuthenticationError):
        await provider.fetch_departures("Master:84902", "", "", 10)


@pytest.mark.parametrize("status", [401, 403])
async def test_http_401_403_raise_authentication_error(status):
    provider = _provider(_FakeSession(status=status))

    with pytest.raises(AuthenticationError):
        await provider.search_stops("Christuskirche")


async def test_non_ok_return_code_is_not_fatal():
    session = _FakeSession(payload={"returnCode": "ERROR_TEXT", "errorText": "Unbekannte Haltestelle"})

    assert await _provider(session).search_stops("Nirgendwo") == []


async def test_http_error_returns_no_departures():
    provider = _provider(_FakeSession(status=500))

    assert await provider.fetch_departures("Master:84902", "", "", 10) is None


# ── stop search ───────────────────────────────────────────────────────────────


async def test_search_stops_keeps_only_stations():
    session = _FakeSession(payload=CHECK_NAME_RESPONSE)

    stops = await _provider(session).search_stops("Christuskirche")

    assert stops == [
        {"id": "Master:84902", "name": "Christuskirche", "place": "Hamburg", "area_type": "stop"}
    ]
    assert session.calls[0].url == "https://gti.geofox.de/gti/public/checkName"


# ── departures ────────────────────────────────────────────────────────────────


async def _departures(limit=10):
    session = _FakeSession(payload=DEPARTURE_LIST_RESPONSE)
    provider = _provider(session)
    data = await provider.fetch_departures("Master:84902", "", "", limit)
    return provider, session, data


async def test_departure_list_request_shape():
    _, session, _ = await _departures()

    body = json.loads(session.calls[0].body.decode("utf-8"))
    assert session.calls[0].url == "https://gti.geofox.de/gti/public/departureList"
    assert body["station"] == {"id": "Master:84902", "type": "STATION"}
    assert body["useRealtime"] is True
    assert body["maxTimeOffset"] == 200
    # GTITime is dd.MM.yyyy / HH:mm, not ISO
    assert len(body["time"]["date"].split(".")) == 3
    assert ":" in body["time"]["time"]


async def test_station_id_is_required():
    provider = _provider(_FakeSession(payload=DEPARTURE_LIST_RESPONSE))

    assert await provider.fetch_departures(None, "Hamburg", "Christuskirche", 10) is None


async def test_departures_are_sorted_and_limited():
    _, _, data = await _departures(limit=2)

    assert [event["line"] for event in data["stopEvents"]] == ["U1", "S1"]


async def test_time_offset_minutes_plus_delay_seconds():
    """planned = board reference + timeOffset; estimated adds the delay.

    Matches how Home Assistant core's own hvv_departures integration reads
    these two fields.
    """
    provider, _, data = await _departures()
    bus = next(event for event in data["stopEvents"] if event["line"] == "476")

    departure = provider.parse_departure(bus, TZ, NOW)

    assert departure.planned_time == "21:48"  # 21:28 + 20 min
    assert departure.departure_time == "21:50"  # + 120 s
    assert departure.delay == 2
    assert departure.is_realtime is True
    assert departure.minutes_until_departure == 22


async def test_realtime_platform_wins_and_flags_a_change():
    provider, _, data = await _departures()
    subway = next(event for event in data["stopEvents"] if event["line"] == "U1")

    departure = provider.parse_departure(subway, TZ, NOW)

    assert departure.platform == "3"
    assert departure.planned_platform == "2"
    assert departure.platform_changed is True
    assert departure.to_dict()["platform_changed"] is True


async def test_unchanged_platform_is_not_a_change():
    provider, _, data = await _departures()
    bus = next(event for event in data["stopEvents"] if event["line"] == "476")

    departure = provider.parse_departure(bus, TZ, NOW)

    assert departure.platform == "5"
    assert departure.platform_changed is False


async def test_cancellation_and_attributes_become_notices():
    provider, _, data = await _departures()
    s_bahn = next(event for event in data["stopEvents"] if event["line"] == "S1")

    departure = provider.parse_departure(s_bahn, TZ, NOW)

    assert "Bauarbeiten" in departure.notices
    assert "Fahrt fällt aus" in departure.notices
    # No `delay` field at all → not real-time controlled
    assert departure.is_realtime is False
    assert departure.delay == 0


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ({"name": "U1", "type": {"simpleType": "TRAIN", "shortInfo": "U"}}, "subway"),
        ({"name": "S1", "type": {"simpleType": "TRAIN", "shortInfo": "S"}}, "train"),
        ({"name": "A1", "type": {"simpleType": "TRAIN", "shortInfo": "AKN"}}, "train"),
        ({"name": "476", "type": {"simpleType": "BUS"}}, "bus"),
        ({"name": "X35", "type": {"simpleType": "BUS", "shortInfo": "XpressBus"}}, "bus"),
        ({"name": "62", "type": {"simpleType": "SHIP", "shortInfo": "Fähre"}}, "ferry"),
        ({"name": "8000", "type": {"simpleType": "BUS", "shortInfo": "AST"}}, "taxi"),
        # Coarse TRAIN with no shortInfo — the line name settles it
        ({"name": "U3", "type": {"simpleType": "TRAIN"}}, "subway"),
        ({"name": "S31", "type": {"simpleType": "TRAIN"}}, "train"),
        ({"name": "???", "type": {}}, "unknown"),
    ],
)
def test_transport_type_mapping(line, expected):
    from openpublictransport.providers.hvv_gti import _transport_type

    assert _transport_type(line) == expected


async def test_departure_without_time_offset_is_skipped():
    session = _FakeSession(
        payload={
            "returnCode": "OK",
            "time": {"date": "01.08.2026", "time": "21:28"},
            "departures": [{"line": {"name": "476"}}],
        }
    )

    data = await _provider(session).fetch_departures("Master:84902", "", "", 10)

    assert data == {"stopEvents": []}


async def test_unparseable_reference_time_falls_back():
    session = _FakeSession(
        payload={
            "returnCode": "OK",
            "time": {"date": "nonsense", "time": "??"},
            "departures": [{"line": {"name": "476", "type": {"simpleType": "BUS"}}, "timeOffset": 5}],
        }
    )

    data = await _provider(session).fetch_departures("Master:84902", "", "", 10)

    assert len(data["stopEvents"]) == 1


# ── registry ──────────────────────────────────────────────────────────────────


def test_provider_is_registered_and_needs_a_key():
    provider = get_provider("hvv_gti", None, api_key="u", api_key_secondary="p")

    assert isinstance(provider, HVVGTIProvider)
    assert provider.requires_api_key is True
    assert provider.get_timezone() == "Europe/Berlin"


def test_efa_hvv_provider_is_untouched():
    """The existing keyless `hvv` provider must keep working — the IDs are load-bearing."""
    from openpublictransport.providers.hvv import HVVProvider

    provider = get_provider("hvv", None)

    assert isinstance(provider, HVVProvider)
    assert provider.requires_api_key is False
