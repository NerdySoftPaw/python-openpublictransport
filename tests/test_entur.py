"""Tests for the Entur (Norway) provider — transmodel GraphQL + geocoder."""

from datetime import datetime
from zoneinfo import ZoneInfo

from openpublictransport.providers.entur import EnturProvider

TZ = ZoneInfo("Europe/Oslo")
NOW = datetime(2026, 7, 14, 19, 0, tzinfo=TZ)


# A geocoder autocomplete response: one stop place, one group, one POI.
GEOCODER = {
    "features": [
        {"properties": {"id": "NSR:StopPlace:59872", "label": "Oslo S, Oslo", "locality": "Oslo"}},
        {"properties": {"id": "NSR:GroupOfStopPlaces:1", "label": "Oslo"}},
        {"properties": {"id": "OSM:TopographicPlace:123", "label": "Espresso House"}},
    ]
}


def test_geocoder_keeps_only_stop_places():
    stops = EnturProvider._stops_from_geocoder(GEOCODER)
    assert stops == [
        {"id": "NSR:StopPlace:59872", "name": "Oslo S, Oslo", "place": "Oslo", "area_type": "stop"}
    ]


def _p():
    return EnturProvider(session=None)


def test_parse_departure_realtime_with_delay():
    call = {
        "aimedDepartureTime": "2026-07-14T19:22:00+02:00",
        "expectedDepartureTime": "2026-07-14T19:25:00+02:00",
        "realtime": True,
        "cancellation": False,
        "destinationDisplay": {"frontText": "Moss"},
        "quay": {"publicCode": "13"},
        "serviceJourney": {"line": {"publicCode": "R21", "transportMode": "rail"}},
    }
    dep = _p().parse_departure(call, TZ, NOW)
    assert dep is not None
    assert dep.line == "R21"
    assert dep.destination == "Moss"
    assert dep.transportation_type == "train"       # rail
    assert dep.planned_time == "19:22"
    assert dep.departure_time == "19:25"
    assert dep.delay == 3
    assert dep.is_realtime is True
    assert dep.platform == "13"


def test_parse_departure_cancellation_and_modes():
    p = _p()
    bus = {
        "aimedDepartureTime": "2026-07-14T19:30:00+02:00",
        "expectedDepartureTime": "2026-07-14T19:30:00+02:00",
        "realtime": False,
        "cancellation": True,
        "destinationDisplay": {"frontText": "Sentrum"},
        "serviceJourney": {"line": {"publicCode": "31", "transportMode": "bus"}},
    }
    dep = p.parse_departure(bus, TZ, NOW)
    assert dep.transportation_type == "bus"
    assert dep.is_realtime is False
    assert dep.notices == ["Cancelled"]
    # mode map spot checks
    for mode, expected in [("metro", "subway"), ("tram", "tram"), ("water", "ferry"), ("coach", "bus")]:
        call = {
            "aimedDepartureTime": "2026-07-14T19:30:00+02:00",
            "expectedDepartureTime": "2026-07-14T19:30:00+02:00",
            "serviceJourney": {"line": {"publicCode": "X", "transportMode": mode}},
        }
        assert p.parse_departure(call, TZ, NOW).transportation_type == expected


def test_missing_time_returns_none():
    assert _p().parse_departure({"serviceJourney": {}}, TZ, NOW) is None


def test_entur_registered():
    from openpublictransport.providers import get_provider_class
    assert get_provider_class("entur_no") is EnturProvider
    assert _p().get_timezone() == "Europe/Oslo"
