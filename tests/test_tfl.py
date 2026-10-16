"""Tests for the TfL Unified API provider."""

from datetime import datetime
from zoneinfo import ZoneInfo

from openpublictransport.providers.tfl import PROVIDER_TFL, TfLProvider, child_stop_ids

TZ = ZoneInfo("Europe/London")
NOW = datetime(2026, 10, 16, 19, 0, tzinfo=TZ)


def _arrival(**overrides):
    a = {
        "id": "-768615047",
        "vehicleId": "201",
        "naptanId": "940GZZLUOXC",
        "stationName": "Oxford Circus Underground Station",
        "lineId": "bakerloo",
        "lineName": "Bakerloo",
        "platformName": "Southbound - Platform 3",
        "destinationNaptanId": "940GZZLUEAC",
        "destinationName": "Elephant & Castle Underground Station",
        "timeToStation": 1010,
        "towards": "Elephant and Castle",
        "expectedArrival": "2026-10-16T18:14:48Z",
        "modeName": "tube",
    }
    a.update(overrides)
    return a


def test_provider_metadata_and_optional_key():
    p = TfLProvider(session=None)
    assert p.provider_id == PROVIDER_TFL == "tfl_uk"
    assert p.get_timezone() == "Europe/London"
    assert p.requires_api_key is False
    assert "app_key" not in p._params()
    assert TfLProvider(session=None, api_key="k")._params({"modes": "tube"}) == {"modes": "tube", "app_key": "k"}


def test_tube_arrival_is_realtime_without_delay():
    dep = TfLProvider(session=None).parse_departure(_arrival(), TZ, NOW)
    assert dep.line == "Bakerloo"
    assert dep.destination == "Elephant & Castle"
    assert dep.departure_time == dep.planned_time == "19:14"  # BST
    assert dep.delay == 0
    assert dep.is_realtime is True
    assert dep.platform == "3"
    assert dep.platform_name == "Southbound - Platform 3"
    assert dep.transportation_type == "subway"
    assert dep.minutes_until_departure == 14


def test_bus_stop_letter_is_the_platform():
    dep = TfLProvider(session=None).parse_departure(
        _arrival(lineName="12", platformName="RC", modeName="bus", destinationName="Oxford Circus"), TZ, NOW
    )
    assert dep.platform == "RC"
    assert dep.transportation_type == "bus"


def test_mode_mapping():
    p = TfLProvider(session=None)
    for mode, expected in [("dlr", "train"), ("overground", "train"), ("elizabeth-line", "train"),
                           ("national-rail", "train"), ("tram", "tram"), ("river-bus", "ferry"), ("x", "unknown")]:
        assert p.parse_departure(_arrival(modeName=mode), TZ, NOW).transportation_type == expected, mode


def test_null_platform_and_missing_time():
    p = TfLProvider(session=None)
    assert p.parse_departure(_arrival(platformName="null"), TZ, NOW).platform is None
    assert p.parse_departure(_arrival(expectedArrival=None), TZ, NOW) is None


def test_merge_sorts_dedupes_and_drops_terminating_trains():
    arrivals = [
        _arrival(id="b", expectedArrival="2026-10-16T18:20:00Z"),
        _arrival(id="a", expectedArrival="2026-10-16T18:10:00Z"),
        _arrival(id="a", expectedArrival="2026-10-16T18:10:00Z"),
        _arrival(id="t", destinationNaptanId="940GZZLUOXC", expectedArrival="2026-10-16T18:05:00Z"),
    ]
    merged = TfLProvider.merge_arrivals(arrivals)
    assert [a["id"] for a in merged] == ["a", "b"]


def test_hub_children_resolution():
    hub = {
        "naptanId": "HUBKGX",
        "children": [
            {"naptanId": "490G000479", "lines": [{"id": "17"}], "children": [
                {"naptanId": "490000129D", "lines": [{"id": "17"}]},
                {"naptanId": "490000129E", "lines": [{"id": "390"}]},
            ]},
            {"naptanId": "940GZZLUKSX", "lines": [{"id": "victoria"}]},
            {"naptanId": "910GKNGX", "lines": [{"id": "lumo"}]},
            {"naptanId": "5100000000", "lines": []},
        ],
    }
    assert child_stop_ids(hub) == ["490000129D", "490000129E", "940GZZLUKSX", "910GKNGX"]


def test_search_results():
    data = {
        "matches": [
            {"id": "940GZZLUOXC", "name": "Oxford Circus Underground Station", "modes": ["tube"]},
            {"id": "490000173RC", "name": "Oxford Circus Station", "towards": "Marble Arch", "modes": ["bus"]},
            {"name": "no id"},
        ]
    }
    stops = TfLProvider.stops_from_search(data)
    assert stops == [
        {"id": "940GZZLUOXC", "name": "Oxford Circus Underground Station", "place": "London", "area_type": "stop"},
        {"id": "490000173RC", "name": "Oxford Circus Station (towards Marble Arch)", "place": "London", "area_type": "stop"},
    ]
