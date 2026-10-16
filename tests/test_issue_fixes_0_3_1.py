"""Regression tests for the issues fixed in 0.3.1 (#92, #99)."""

from datetime import datetime
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest

from openpublictransport.parsers import rail_line_label
from openpublictransport.providers.nvbw import NVBWProvider
from openpublictransport.providers.transitous import TransitousProvider
from openpublictransport.providers.vbn import VBNOTPProvider

TZ = ZoneInfo("Europe/Berlin")
NOW = datetime(2026, 10, 16, 19, 0, tzinfo=TZ)


# -- #99: train category for long-distance trains ----------------------------------

@pytest.mark.parametrize("number, name, expected", [
    ("620", "ICE 620 InterCityExpress", "ICE 620"),
    ("2045", "IC 2045 InterCity", "IC 2045"),
    ("5", "RE 5 RegionalExpress", "RE 5"),
    ("RE5", "RE5 RegionalExpress", "RE5"),        # already readable
    ("11", "Bus 11", "11"),                       # buses stay bare
    ("4", "Straßenbahn 4", "4"),                  # trams too
    ("620", "", "620"),
    ("620", "ICE 621 InterCityExpress", "620"),   # number mismatch: keep
])
def test_rail_line_label(number, name, expected):
    assert rail_line_label(number, name) == expected


def test_efa_long_distance_train_shows_category():
    event = {
        "departureTimePlanned": "2026-10-16T17:10:00Z",
        "departureTimeEstimated": "2026-10-16T17:12:00Z",
        "realtimeStatus": ["MONITORED"],
        "transportation": {
            "number": "1605",
            "name": "ICE 1605 InterCityExpress",
            "destination": {"name": "München Hbf"},
            "product": {"class": 16, "name": "ICE"},
        },
    }
    dep = NVBWProvider(session=None).parse_departure(event, TZ, NOW)
    assert dep.line == "ICE 1605"


def _transitous_event(mode, short, display):
    return {
        "place": {"departure": "2026-10-16T17:10:00Z", "scheduledDeparture": "2026-10-16T17:10:00Z", "tz": "Europe/Berlin"},
        "mode": mode, "routeShortName": short, "displayName": display, "headsign": "Berlin Hbf", "realTime": True,
    }


@pytest.mark.parametrize("mode, short, display, expected", [
    ("HIGHSPEED_RAIL", "29", "ICE 1105", "ICE 1105"),
    ("LONG_DISTANCE", "28", "IC 2045", "IC 2045"),
    ("REGIONAL_RAIL", "RB52", "RB52 (16294)", "RB52"),
    ("BUS", "151", "Bus 151", "151"),
])
def test_transitous_long_distance_uses_display_name(mode, short, display, expected):
    dep = TransitousProvider(session=None).parse_departure(_transitous_event(mode, short, display), TZ, NOW)
    assert dep is not None
    assert dep.line == expected


# -- #92: OTP REST departure window ----------------------------------------------

async def test_otp_rest_requests_a_wide_departure_window():
    provider = VBNOTPProvider(session=None, api_key="key")
    calls = []

    async def fake_get(url, params=None):
        calls.append((url, params))
        return []

    provider._get = fake_get
    await provider.fetch_departures("1:000123", "", "", 10)

    stoptimes = next(params for url, params in calls if url.endswith("/stoptimes"))
    assert stoptimes["timeRange"] >= 12 * 3600
    assert stoptimes["numberOfDepartures"] == 10
