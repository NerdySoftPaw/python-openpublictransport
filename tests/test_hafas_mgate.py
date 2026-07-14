"""Tests for the modern HAFAS ``mgate.exe`` base provider."""

from datetime import datetime
from zoneinfo import ZoneInfo

from openpublictransport.providers.bart import BARTProvider
from openpublictransport.providers.irishrail import IrishRailProvider
from openpublictransport.providers.hafas_mgate_base import _hafas_datetime, _platform

TZ = ZoneInfo("America/Los_Angeles")
NOW = datetime(2026, 7, 14, 10, 0, tzinfo=TZ)


# -- low-level helpers ------------------------------------------------------

def test_hafas_datetime_normal_and_day_offset():
    dt = _hafas_datetime("20260714", "103800", TZ)
    assert (dt.hour, dt.minute) == (10, 38) and dt.day == 14
    # leading day-offset ("01" + HHMMSS) rolls to the next day
    dt2 = _hafas_datetime("20260714", "01003000", TZ)
    assert dt2.day == 15 and (dt2.hour, dt2.minute) == (0, 30)
    assert _hafas_datetime("", "103800", TZ) is None


def test_platform_string_and_structured_forms():
    assert _platform({"dPlatfS": "5"}, realtime=False) == "5"
    assert _platform({"dPltfS": {"type": "PL", "txt": "1"}}, realtime=False) == "1"
    # realtime platform preferred when present
    assert _platform({"dPltfS": {"txt": "1"}, "dPltfR": {"txt": "9"}}, realtime=True) == "9"


# -- LocMatch parsing -------------------------------------------------------

def test_stops_from_locmatch():
    res = {"match": {"locL": [
        {"lid": "A=1@L=1234@", "extId": "1234", "name": "Powell Street"},
        {"name": "no id"},  # dropped
    ]}}
    stops = BARTProvider._stops_from_locmatch(res)
    assert stops == [{"id": "A=1@L=1234@", "name": "Powell Street", "place": "", "area_type": "stop"}]
    assert BARTProvider._stops_from_locmatch({}) == []


# -- departure parsing ------------------------------------------------------

def _bart_event():
    # BART: catOut "Metro" -> subway; realtime +3
    return {
        "date": "20260714",
        "dirTxt": "SF / Daly City",
        "stbStop": {"dTimeS": "103800", "dTimeR": "104100", "dPltfS": {"txt": "1"}},
        "_prod": {"name": "Green-S", "cls": 128, "prodCtx": {"catOut": "Metro   ", "catCode": "7"}},
    }


def test_parse_bart_metro_is_subway_with_delay():
    dep = BARTProvider(session=None).parse_departure(_bart_event(), TZ, NOW)
    assert dep is not None
    assert dep.line == "Green-S"
    assert dep.destination == "SF / Daly City"
    assert dep.transportation_type == "subway"      # catOut "Metro"
    assert dep.planned_time == "10:38"
    assert dep.departure_time == "10:41"
    assert dep.delay == 3
    assert dep.is_realtime is True
    assert dep.platform == "1"


def test_parse_irishrail_dart_is_train_and_cancellation():
    tz = ZoneInfo("Europe/Dublin")
    now = datetime(2026, 7, 14, 18, 0, tzinfo=tz)
    event = {
        "date": "20260714",
        "dirTxt": "Bray (Daly)",
        "isCncl": True,
        "stbStop": {"dTimeS": "184100", "dCncl": True},
        "_prod": {"name": "DART", "cls": 16, "prodCtx": {"catOut": "DART    "}},
    }
    dep = IrishRailProvider(session=None).parse_departure(event, tz, now)
    assert dep.transportation_type == "train"       # catOut "DART" -> train
    assert dep.is_realtime is False                  # no dTimeR
    assert dep.notices == ["Cancelled"]


def test_transport_type_catout_mapping_and_stems():
    p = BARTProvider(session=None)
    assert p._transport_type({"prodCtx": {"catOut": "Bus     "}}) == "bus"
    assert p._transport_type({"prodCtx": {"catOut": "TER"}}) == "train"
    assert p._transport_type({"prodCtx": {"catOut": "Cable Car"}}) == "tram"   # stem
    assert p._transport_type({"prodCtx": {"catOut": "???"}}) == "unknown"


def test_mgate_providers_registered():
    from openpublictransport.providers import get_provider_class
    assert get_provider_class("bart_us") is BARTProvider
    assert get_provider_class("irishrail_ie") is IrishRailProvider
    assert BARTProvider(session=None).get_timezone() == "America/Los_Angeles"
