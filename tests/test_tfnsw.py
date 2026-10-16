"""Tests for the Transport for NSW (EFA rapidJSON) provider."""

import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from openpublictransport.exceptions import AuthenticationError
from openpublictransport.providers.tfnsw import PROVIDER_TFNSW, TfNSWProvider

TZ = ZoneInfo("Australia/Sydney")
NOW = datetime(2026, 10, 17, 8, 0, tzinfo=TZ)

# Shape of a TfNSW departure_mon rapidJSON stopEvent (version 10.2.1.42).
TRAIN_EVENT = {
    "isRealtimeControlled": True,
    "location": {
        "id": "2000338",
        "isGlobalId": True,
        "name": "Central Station, Platform 18",
        "disassembledName": "Platform 18",
        "type": "platform",
        "coord": [-33.882, 151.206],
        "properties": {"stopId": "10101100", "area": "4", "platform": "18"},
        "parent": {"id": "10101100", "name": "Central Station", "type": "stop"},
    },
    "departureTimePlanned": "2026-10-16T21:10:00Z",
    "departureTimeBaseTimetable": "2026-10-16T21:10:00Z",
    "departureTimeEstimated": "2026-10-16T21:13:00Z",
    "transportation": {
        "id": "nsw:020T1: :H:sj2",
        "name": "Sydney Trains Network T1 North Shore & Western Line",
        "disassembledName": "T1",
        "number": "T1 North Shore & Western Line",
        "iconId": 1,
        "description": "Emu Plains or Richmond to Berowra",
        "product": {"id": 1, "class": 1, "name": "Sydney Trains Network", "iconId": 1},
        "operator": {"id": "x0001", "name": "Sydney Trains"},
        "destination": {"id": "10101331", "name": "Emu Plains", "type": "stop"},
    },
    "infos": [{"subtitle": "Trackwork this weekend"}],
}

BUS_EVENT = {
    "isRealtimeControlled": False,
    "location": {"id": "200039", "name": "Central Station, Stand A", "disassembledName": "Stand A", "type": "platform"},
    "departureTimePlanned": "2026-10-16T21:20:00Z",
    "transportation": {
        "disassembledName": "333",
        "number": "333",
        "product": {"class": 5, "name": "Sydney Buses Network"},
        "destination": {"name": "North Bondi"},
    },
}


def test_provider_metadata():
    p = TfNSWProvider(session=None, api_key="k")
    assert p.provider_id == PROVIDER_TFNSW == "tfnsw_au"
    assert p.get_timezone() == "Australia/Sydney"
    assert p.requires_api_key is True
    assert p.dm_base_url == "https://api.transport.nsw.gov.au/v1/tp/departure_mon"
    assert p.sf_base_url == "https://api.transport.nsw.gov.au/v1/tp/stop_finder"
    assert p._headers()["Authorization"] == "apikey k"


def test_departure_request_shape_matches_tfnsw_docs():
    params = TfNSWProvider(session=None, api_key="k").departure_params("200060", NOW)
    assert params == {
        "outputFormat": "rapidJSON",
        "coordOutputFormat": "EPSG:4326",
        "mode": "direct",
        "type_dm": "stop",
        "name_dm": "200060",
        "depArrMacro": "dep",
        "itdDate": "20261017",
        "itdTime": "0800",
        "departureMonitorMacro": "true",
        "TfNSWDM": "true",
        "version": "10.2.1.42",
    }
    sf = TfNSWProvider.stop_finder_params("Central")
    assert sf["TfNSWSF"] == "true" and sf["name_sf"] == "Central" and sf["outputFormat"] == "rapidJSON"


def test_missing_key_raises_before_any_request():
    p = TfNSWProvider(session=None)
    with pytest.raises(AuthenticationError):
        asyncio.run(p.search_stops("Central"))


def test_train_departure():
    dep = TfNSWProvider(session=None).parse_departure(TRAIN_EVENT, TZ, NOW)
    assert dep.line == "T1"
    assert dep.description == "Emu Plains or Richmond to Berowra"
    assert dep.destination == "Emu Plains"
    assert dep.planned_time == "08:10"  # AEDT, UTC+11
    assert dep.departure_time == "08:13"
    assert dep.delay == 3
    assert dep.is_realtime is True
    assert dep.platform == "18"
    assert dep.transportation_type == "train"
    assert dep.agency == "Sydney Trains"
    assert dep.notices == ["Trackwork this weekend"]


def test_bus_without_realtime():
    dep = TfNSWProvider(session=None).parse_departure(BUS_EVENT, TZ, NOW)
    assert dep.line == "333"
    assert dep.transportation_type == "bus"
    assert dep.is_realtime is False
    assert dep.delay == 0
    assert dep.platform == "Stand A"


def test_product_classes():
    p = TfNSWProvider(session=None)
    for cls, expected in [(1, "train"), (2, "subway"), (4, "tram"), (5, "bus"), (7, "bus"), (9, "ferry"), (11, "bus")]:
        event = {**BUS_EVENT, "transportation": {**BUS_EVENT["transportation"], "product": {"class": cls, "name": ""}}}
        assert p.parse_departure(event, TZ, NOW).transportation_type == expected, cls


def test_stop_finder_results():
    data = {
        "locations": [
            {"id": "streetX", "name": "Central St, Sydney", "type": "street"},
            {"id": "2000441", "name": "Central Station, Sydney", "disassembledName": "Central Station", "type": "stop",
             "matchQuality": 800, "parent": {"name": "Sydney", "type": "locality"}},
            {"id": "200060", "name": "Central Station, Sydney", "disassembledName": "Central Station", "type": "stop",
             "isBest": True, "matchQuality": 950, "parent": {"name": "Sydney", "type": "locality"}},
        ]
    }
    assert TfNSWProvider.stops_from_finder(data) == [
        {"id": "200060", "name": "Central Station", "place": "Sydney", "area_type": "stop"},
        {"id": "2000441", "name": "Central Station", "place": "Sydney", "area_type": "stop"},
    ]
