"""Tests for the Digitransit (Finland, OTP2 GraphQL) provider."""

from datetime import datetime
from zoneinfo import ZoneInfo

from openpublictransport.providers.digitransit import PROVIDER_DIGITRANSIT, ROUTER_URLS, DigitransitProvider

TZ = ZoneInfo("Europe/Helsinki")
# serviceDay 2026-10-16 00:00 Europe/Helsinki (UTC+3) = 2026-10-15T21:00Z
SERVICE_DAY = 1792098000
NOW = datetime(2026, 10, 16, 21, 0, tzinfo=TZ)

STOPTIME = {
    "serviceDay": SERVICE_DAY,
    "scheduledDeparture": 21 * 3600 + 5 * 60,
    "realtimeDeparture": 21 * 3600 + 7 * 60,
    "departureDelay": 120,
    "realtime": True,
    "headsign": "Vuosaari",
    "trip": {"route": {"shortName": "M1", "mode": "SUBWAY", "color": "FF6319", "agency": {"name": "Helsingin seudun liikenne"}}},
}


def test_provider_metadata():
    p = DigitransitProvider(session=None, api_key="k")
    assert p.provider_id == PROVIDER_DIGITRANSIT == "digitransit_fi"
    assert p.get_timezone() == "Europe/Helsinki"
    assert p.requires_api_key is True
    assert p._auth_headers()["digitransit-subscription-key"] == "k"
    assert "X-API-Key" not in p._auth_headers()


def test_graphql_url_and_router_shorthands():
    assert DigitransitProvider(session=None).graphql_url == "https://api.digitransit.fi/routing/v2/finland/gtfs/v1"
    assert DigitransitProvider(session=None, custom_url="hsl").graphql_url == ROUTER_URLS["hsl"]
    assert DigitransitProvider(session=None, custom_url="https://example.org/gql/").graphql_url == "https://example.org/gql"


def test_stoptime_is_parsed():
    p = DigitransitProvider(session=None)
    [event] = p._stoptimes_to_events([STOPTIME])
    dep = p.parse_departure(event, TZ, NOW)
    assert dep.line == "M1"
    assert dep.destination == "Vuosaari"
    assert dep.planned_time == "21:05"
    assert dep.departure_time == "21:07"
    assert dep.delay == 2
    assert dep.is_realtime is True
    assert dep.transportation_type == "subway"
    assert dep.agency == "Helsingin seudun liikenne"
    assert dep.line_color == "#FF6319"
    assert dep.minutes_until_departure == 7


def test_mode_mapping():
    p = DigitransitProvider(session=None)
    for mode, expected in [("BUS", "bus"), ("TRAM", "tram"), ("RAIL", "train"), ("FERRY", "ferry"), ("TROLLEYBUS", "bus")]:
        st = {**STOPTIME, "trip": {"route": {"shortName": "x", "mode": mode}}}
        [event] = p._stoptimes_to_events([st])
        assert p.parse_departure(event, TZ, NOW).transportation_type == expected, mode


def test_search_groups_platforms_of_a_station():
    raw = [
        {"gtfsId": "HSL:1020601", "name": "Rautatientori", "lat": 60.1709, "lon": 24.9427,
         "parentStation": {"gtfsId": "HSL:1000003", "name": "Rautatientori"}},
        {"gtfsId": "HSL:1020602", "name": "Rautatientori", "lat": 60.1709, "lon": 24.9428,
         "parentStation": {"gtfsId": "HSL:1000003", "name": "Rautatientori"}},
        {"gtfsId": "HSL:1020453", "name": "Rautatientori", "lat": 60.1712, "lon": 24.9439, "parentStation": None},
    ]
    grouped = DigitransitProvider(session=None)._group_by_name(raw)
    ids = sorted(g["id"] for g in grouped)
    assert ids == ["HSL:1020453", "HSL:1020601|HSL:1020602"]
