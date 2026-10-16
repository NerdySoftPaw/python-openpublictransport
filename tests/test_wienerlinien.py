"""Tests for the Wiener Linien (Vienna OGD realtime) provider."""

from datetime import datetime
from zoneinfo import ZoneInfo

from openpublictransport.providers.wienerlinien import (
    ATTRIBUTION,
    PROVIDER_WIENERLINIEN,
    WienerLinienProvider,
    _monitor_params,
)

TZ = ZoneInfo("Europe/Vienna")
NOW = datetime(2026, 10, 16, 20, 0, tzinfo=TZ)


def _dep(planned, real=None, countdown=3, vtype="ptMetro", name="U1", towards="Leopoldau", platform="1"):
    times = {"timePlanned": planned, "countdown": countdown}
    if real:
        times["timeReal"] = real
    return {
        "departureTime": times,
        "vehicle": {"name": name, "towards": towards, "platform": platform, "type": vtype, "barrierFree": True},
    }


MONITOR = {
    "data": {
        "monitors": [
            {
                "locationStop": {"properties": {"name": "60201320", "title": "Stephansplatz", "attributes": {"rbl": 4111}}},
                "lines": [
                    {
                        "name": "U1",
                        "towards": "Leopoldau",
                        "platform": "1",
                        "type": "ptMetro",
                        "barrierFree": True,
                        "departures": {
                            "departure": [
                                _dep("2026-10-16T20:06:00.000+0200", "2026-10-16T20:04:30.000+0200"),
                                _dep("2026-10-16T20:11:00.000+0200", "2026-10-16T20:13:00.000+0200"),
                            ]
                        },
                    }
                ],
            },
            {
                "locationStop": {"properties": {"name": "60201320", "title": "Stephansplatz U", "attributes": {"rbl": 2903}}},
                "lines": [
                    {
                        "name": "3A",
                        "towards": "Schottenring U",
                        "platform": "1",
                        "type": "ptBusCity",
                        "departures": {
                            "departure": [
                                {
                                    "departureTime": {"timePlanned": "2026-10-16T20:05:00.000+0200", "countdown": 5}
                                }
                            ]
                        },
                    }
                ],
            },
        ],
        "trafficInfos": [{"title": "Fahrtbehinderung\nSchadhafter Bus", "relatedLines": ["3A"]}],
    },
    "message": {"value": "OK", "messageCode": 1},
}


def test_provider_metadata():
    p = WienerLinienProvider(session=None)
    assert p.provider_id == PROVIDER_WIENERLINIEN == "wienerlinien_at"
    assert p.get_timezone() == "Europe/Vienna"
    assert p.requires_api_key is False
    assert "data.wien.gv.at" in ATTRIBUTION


def test_monitor_params_use_diva_for_stations_and_stopid_for_rbl():
    params = _monitor_params("60201320|4111")
    assert ("diva", "60201320") in params
    assert ("stopId", "4111") in params


def test_events_are_flattened_with_line_level_fallbacks_and_notices():
    events = WienerLinienProvider.events_from_monitor(MONITOR)
    assert len(events) == 3
    bus = next(e for e in events if e["line"] == "3A")
    assert bus["type"] == "ptBusCity"
    assert bus["towards"] == "Schottenring U"
    assert bus["notices"] == ["Fahrtbehinderung Schadhafter Bus"]


def test_realtime_departure_with_delay():
    p = WienerLinienProvider(session=None)
    events = p.events_from_monitor(MONITOR)
    late = p.parse_departure(events[1], TZ, NOW)
    assert late.line == "U1"
    assert late.planned_time == "20:11"
    assert late.departure_time == "20:13"
    assert late.delay == 2
    assert late.is_realtime is True
    assert late.transportation_type == "subway"
    assert late.platform == "1"
    assert late.minutes_until_departure == 13


def test_departure_without_time_real_is_scheduled():
    p = WienerLinienProvider(session=None)
    bus = next(e for e in p.events_from_monitor(MONITOR) if e["line"] == "3A")
    dep = p.parse_departure(bus, TZ, NOW)
    assert dep.is_realtime is False
    assert dep.delay == 0
    assert dep.departure_time == dep.planned_time == "20:05"
    assert dep.transportation_type == "bus"


def test_extra_trip_without_planned_time_uses_real_time():
    p = WienerLinienProvider(session=None)
    dep = p.parse_departure({"line": "U4", "towards": "Hütteldorf", "type": "ptMetro", "timeReal": "2026-10-16T20:07:58.000+0200"}, TZ, NOW)
    assert dep.planned_time == dep.departure_time == "20:07"
    assert dep.is_realtime is True


def test_type_mapping():
    p = WienerLinienProvider(session=None)
    for vtype, expected in [("ptTram", "tram"), ("ptBusNight", "bus"), ("ptTrainS", "train"), ("ptTramWLB", "tram"), ("weird", "unknown")]:
        dep = p.parse_departure({"line": "X", "type": vtype, "timePlanned": "2026-10-16T20:05:00.000+0200"}, TZ, NOW)
        assert dep.transportation_type == expected, vtype


def test_missing_times_are_skipped():
    assert WienerLinienProvider(session=None).parse_departure({"line": "U1"}, TZ, NOW) is None


CSV = (
    "﻿DIVA;PlatformText;Municipality;MunicipalityID;Longitude;Latitude\r\n"
    "60201320;Stephansplatz;Wien;49000001;16.37;48.20\r\n"
    "60200657;Karlsplatz;Wien;49000001;16.36;48.20\r\n"
    "60200975;Oper, Karlsplatz;Wien;49000001;16.36;48.20\r\n"
    "60201040;Westbahnhof;Wien;49000001;16.33;48.19\r\n"
    "60200123;Schwedenplatz;Wien;49000001;16.37;48.21\r\n"
    "60209999;Zieglergasse/Straße;Wien;49000001;16.34;48.19\r\n"
)


def test_station_csv_search_ranks_exact_match_first():
    stations = WienerLinienProvider.parse_stations_csv(CSV)
    assert stations[0] == {"id": "60201320", "name": "Stephansplatz", "place": "Wien"}
    hits = WienerLinienProvider.filter_stations(stations, "karlsplatz")
    assert [h["id"] for h in hits] == ["60200657", "60200975"]
    assert hits[0]["area_type"] == "stop"


def test_station_search_is_diacritic_and_eszett_insensitive():
    stations = WienerLinienProvider.parse_stations_csv(CSV)
    assert WienerLinienProvider.filter_stations(stations, "strasse")[0]["id"] == "60209999"
    assert WienerLinienProvider.filter_stations(stations, "") == []
