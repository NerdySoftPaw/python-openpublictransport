"""Tests for the SL Transport API (Stockholm) provider."""

from datetime import datetime
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

from openpublictransport.providers.sl import PROVIDER_ID, SLProvider

TZ = ZoneInfo("Europe/Stockholm")
NOW = datetime(2026, 10, 16, 19, 50, tzinfo=TZ)

SITES = [
    {"id": 102, "name": "Styrsvik", "note": "Runmarö"},
    {"id": 9001, "name": "T-Centralen", "alias": ["Tcentralen"], "abbreviation": "TCE"},
    {"id": 9208, "name": "Slussen", "note": "ersättningstrafik"},
    {"id": 9192, "name": "Slussen", "abbreviation": "SLU"},
    {"id": 1080, "name": "Södermalmstorg"},
    {"id": 5000, "name": "Gamla stan"},
]


def _event(**overrides):
    event = {
        "destination": "Mörby centrum",
        "direction": "Mörby centrum",
        "state": "EXPECTED",
        "scheduled": "2026-10-16T19:57:30",
        "expected": "2026-10-16T19:57:30",
        "journey": {"id": 1, "state": "NORMALPROGRESS", "prediction_state": "NORMAL"},
        "stop_point": {"id": 1051, "name": "T-Centralen", "designation": "3"},
        "line": {"id": 14, "designation": "14", "transport_mode": "METRO", "group_of_lines": "Tunnelbanans röda linje"},
        "deviations": [],
    }
    event.update(overrides)
    return event


def test_provider_metadata():
    p = SLProvider(session=None)
    assert p.provider_id == PROVIDER_ID == "sl_se"
    assert p.get_timezone() == "Europe/Stockholm"
    assert p.requires_api_key is False


def test_tracked_on_time_departure():
    dep = SLProvider(session=None).parse_departure(_event(), TZ, NOW)
    assert dep.line == "14"
    assert dep.destination == "Mörby centrum"
    assert dep.planned_time == dep.departure_time == "19:57"
    assert dep.delay == 0
    assert dep.platform == "3"
    assert dep.transportation_type == "subway"
    assert dep.is_realtime is True
    assert dep.minutes_until_departure == 7
    assert dep.notices is None
    assert dep.departure_time_obj.tzinfo is not None


def test_delayed_departure():
    dep = SLProvider(session=None).parse_departure(_event(expected="2026-10-16T20:01:12", journey={}), TZ, NOW)
    assert dep.planned_time == "19:57"
    assert dep.departure_time == "20:01"
    assert dep.delay == 4
    assert dep.is_realtime is True


def test_untracked_timetable_departure_is_not_realtime():
    dep = SLProvider(session=None).parse_departure(_event(journey={"id": 2, "state": "EXPECTED"}), TZ, NOW)
    assert dep.is_realtime is False


def test_missing_expected_falls_back_to_schedule():
    event = _event()
    del event["expected"]
    dep = SLProvider(session=None).parse_departure(event, TZ, NOW)
    assert dep.departure_time == "19:57"
    assert dep.is_realtime is False


def test_naive_times_are_stockholm_and_converted():
    dep = SLProvider(session=None).parse_departure(_event(), ZoneInfo("UTC"), NOW)
    assert dep.planned_time == "17:57"


def test_cancelled_with_deviation_messages():
    dep = SLProvider(session=None).parse_departure(
        _event(
            state="CANCELLED",
            deviations=[
                {"consequence": "CANCELLED", "message": "Inställd"},
                {"consequence": "INFORMATION", "message": "Förseningar"},
            ],
        ),
        TZ,
        NOW,
    )
    assert dep.notices == ["Cancelled", "Förseningar"]


def test_transport_modes():
    expected = {"BUS": "bus", "METRO": "subway", "TRAIN": "train", "TRAM": "tram", "SHIP": "ferry", "FERRY": "ferry", "XYZ": "unknown"}
    for mode, unified in expected.items():
        dep = SLProvider(session=None).parse_departure(_event(line={"designation": "1", "transport_mode": mode}), TZ, NOW)
        assert dep.transportation_type == unified, mode


def test_missing_times_are_skipped():
    event = _event()
    del event["scheduled"]
    del event["expected"]
    assert SLProvider(session=None).parse_departure(event, TZ, NOW) is None


async def test_search_caches_and_ranks():
    p = SLProvider(session=None)
    p._request = AsyncMock(return_value=SITES)

    tc = await p.search_stops("t-centralen")
    assert tc[0] == {"id": "9001", "name": "T-Centralen", "place": "", "area_type": "stop"}
    assert (await p.search_stops("Tcentralen"))[0]["id"] == "9001"
    sl = await p.search_stops("Slussen")
    assert [r["id"] for r in sl] == ["9192", "9208"]
    assert sl[1]["place"] == "ersättningstrafik"
    assert p._request.await_count == 1


async def test_search_is_accent_insensitive_and_prefers_prefix():
    p = SLProvider(session=None)
    p._request = AsyncMock(return_value=SITES)
    results = await p.search_stops("soder")
    assert results[0]["id"] == "1080"
    results = await p.search_stops("stan")
    assert results[0]["id"] == "5000"


async def test_fetch_departures_builds_request_and_limits():
    p = SLProvider(session=None)
    p._request = AsyncMock(return_value={"departures": [_event() for _ in range(5)], "stop_deviations": []})
    result = await p.fetch_departures("9001", "", "", 3)
    assert len(result["stopEvents"]) == 3
    args, kwargs = p._request.await_args
    assert args == ("get", "https://transport.integration.sl.se/v1/sites/9001/departures")
    assert kwargs["params"] == {"forecast": 60}


def test_delay_matches_displayed_minutes():
    dep = SLProvider(session=None).parse_departure(
        _event(scheduled="2026-10-16T19:58:47", expected="2026-10-16T19:59:00"), TZ, NOW
    )
    assert (dep.planned_time, dep.departure_time, dep.delay) == ("19:58", "19:59", 1)
