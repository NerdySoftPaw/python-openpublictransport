"""Tests for the SBB (transport.opendata.ch) provider."""

from datetime import datetime
from zoneinfo import ZoneInfo

from openpublictransport.providers.sbb import SBBProvider

TZ = ZoneInfo("Europe/Zurich")
NOW = datetime(2026, 10, 16, 19, 0, tzinfo=TZ)


def _event(departure="2026-10-16T19:16:00+0200", delay=None, prognosis=None, category="S", number="6"):
    stop = {"departure": departure, "delay": delay, "platform": "31", "prognosis": prognosis or {}}
    return {"stop": stop, "category": category, "number": number, "to": "Baden", "operator": "SBB"}


def test_delayed_departure_keeps_timetable_time_as_planned():
    dep = SBBProvider(session=None).parse_departure(
        _event(delay=1, prognosis={"departure": "2026-10-16T19:17:00+0200"}), TZ, NOW
    )
    assert dep.planned_time == "19:16"
    assert dep.departure_time == "19:17"
    assert dep.delay == 1
    assert dep.is_realtime is True


def test_delay_without_prognosis_time_is_added_to_the_timetable():
    dep = SBBProvider(session=None).parse_departure(_event(delay=3), TZ, NOW)
    assert dep.planned_time == "19:16"
    assert dep.departure_time == "19:19"
    assert dep.is_realtime is False


def test_prognosis_without_delay_field_derives_the_delay():
    dep = SBBProvider(session=None).parse_departure(
        _event(prognosis={"departure": "2026-10-16T19:20:00+0200"}), TZ, NOW
    )
    assert dep.delay == 4


def test_on_time_departure():
    dep = SBBProvider(session=None).parse_departure(_event(delay=0), TZ, NOW)
    assert dep.planned_time == dep.departure_time == "19:16"
    assert dep.delay == 0


def test_swiss_regional_categories_are_trains():
    for category in ("R", "IRE", "PE", "EXT", "SN", "EN", "NJ"):
        dep = SBBProvider(session=None).parse_departure(_event(delay=0, category=category), TZ, NOW)
        assert dep.transportation_type == "train", category


def test_missing_departure_is_skipped():
    assert SBBProvider(session=None).parse_departure(_event(departure=None), TZ, NOW) is None
