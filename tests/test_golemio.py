"""Tests for the Golemio PID (Prague) provider."""

import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from openpublictransport.exceptions import AuthenticationError
from openpublictransport.providers.golemio import PROVIDER_GOLEMIO, GolemioProvider, departure_params

TZ = ZoneInfo("Europe/Prague")
NOW = datetime(2026, 10, 16, 20, 0, tzinfo=TZ)


def _departure(**overrides):
    d = {
        "arrival_timestamp": {"predicted": "2026-10-16T18:05:30.000Z", "scheduled": "2026-10-16T18:03:00.000Z"},
        "departure_timestamp": {
            "predicted": "2026-10-16T18:05:40.000Z",
            "scheduled": "2026-10-16T18:03:00.000Z",
            "minutes": "6",
        },
        "delay": {"is_available": True, "minutes": 2, "seconds": 160},
        "last_stop": {"name": "Národní třída", "id": "U539Z2P"},
        "route": {"short_name": "9", "type": 0, "is_night": False, "is_regional": False, "is_substitute_transport": False},
        "stop": {"id": "U1072Z3P", "platform_code": "C"},
        "trip": {
            "direction": None,
            "headsign": "Sídliště Řepy",
            "id": "9_1234_261016",
            "is_at_stop": False,
            "is_canceled": False,
            "is_wheelchair_accessible": True,
            "is_air_conditioned": True,
            "short_name": None,
        },
    }
    d.update(overrides)
    return d


def test_provider_metadata():
    p = GolemioProvider(session=None, api_key="k")
    assert p.provider_id == PROVIDER_GOLEMIO == "golemio_cz"
    assert p.get_timezone() == "Europe/Prague"
    assert p.requires_api_key is True
    assert p._headers()["x-access-token"] == "k"
    with pytest.raises(AuthenticationError):
        asyncio.run(GolemioProvider(session=None).fetch_departures("1072", "", "", 5))


def test_departure_params_route_ids_by_kind():
    params = departure_params("1072|1040/1|U142Z1P", 10)
    assert ("aswIds[]", "1072") in params
    assert ("aswIds[]", "1040_1") in params
    assert ("ids[]", "U142Z1P") in params
    assert ("limit", "10") in params
    assert ("skip[]", "canceled") in params


def test_realtime_tram_departure():
    dep = GolemioProvider(session=None).parse_departure(_departure(), TZ, NOW)
    assert dep.line == "9"
    assert dep.destination == "Sídliště Řepy"
    assert dep.planned_time == "20:03"
    assert dep.departure_time == "20:05"
    assert dep.delay == 2
    assert dep.is_realtime is True
    assert dep.platform == "C"
    assert dep.transportation_type == "tram"
    assert dep.minutes_until_departure == 5


def test_departure_without_delay_info():
    raw = _departure(
        delay={"is_available": False, "minutes": None, "seconds": None},
        departure_timestamp={"predicted": "2026-10-16T18:03:00.000Z", "scheduled": "2026-10-16T18:03:00.000Z"},
    )
    dep = GolemioProvider(session=None).parse_departure(raw, TZ, NOW)
    assert dep.is_realtime is False
    assert dep.delay == 0
    assert dep.departure_time == dep.planned_time == "20:03"


def test_null_predicted_falls_back_to_scheduled_and_offset_timestamps():
    raw = _departure(
        delay={"is_available": False, "minutes": None, "seconds": None},
        departure_timestamp={"predicted": None, "scheduled": "2026-10-16T20:10:00+02:00"},
    )
    dep = GolemioProvider(session=None).parse_departure(raw, TZ, NOW)
    assert dep.departure_time == dep.planned_time == "20:10"


def test_route_types():
    p = GolemioProvider(session=None)
    for rtype, expected in [(0, "tram"), (1, "subway"), (2, "train"), (3, "bus"), (4, "ferry"), (7, "train"), (11, "bus"), (99, "unknown")]:
        raw = _departure(route={"short_name": "X", "type": rtype})
        assert p.parse_departure(raw, TZ, NOW).transportation_type == expected, rtype


def test_infotexts_become_notices_and_missing_time_is_skipped():
    events = GolemioProvider.events_from_board(
        {"departures": [_departure()], "infotexts": [{"text": "Výluka  tramvají", "text_en": "Tram closure"}]}
    )
    dep = GolemioProvider(session=None).parse_departure(events[0], TZ, NOW)
    assert dep.notices == ["Výluka tramvají"]
    assert GolemioProvider(session=None).parse_departure(_departure(departure_timestamp={}), TZ, NOW) is None


STOPS_JSON = {
    "stopGroups": [
        {"name": "Můstek", "uniqueName": "Můstek", "node": 1072, "municipality": "Praha", "stops": []},
        {"name": "Anděl", "uniqueName": "Anděl", "node": 1040, "municipality": "Praha", "stops": []},
        {"name": "Andělská Hora,chaty", "uniqueName": "Andělská Hora,chaty", "node": 32267, "municipality": "Andělská Hora"},
        {"name": "Hlavní nádraží", "uniqueName": "Hlavní nádraží", "node": 142, "municipality": "Praha"},
        {"name": "Plzeň,Terminál Hlavní nádraží", "node": 31799, "municipality": "Plzeň"},
    ]
}


def test_stop_search_is_diacritic_insensitive_and_prefers_exact_prague_matches():
    stops = GolemioProvider.parse_stop_groups(STOPS_JSON)
    assert GolemioProvider.filter_stops(stops, "mustek") == [
        {"id": "1072", "name": "Můstek", "place": "Praha", "area_type": "stop"}
    ]
    assert [s["id"] for s in GolemioProvider.filter_stops(stops, "andel")] == ["1040", "32267"]
    assert [s["id"] for s in GolemioProvider.filter_stops(stops, "Hlavní nádraží")] == ["142", "31799"]
