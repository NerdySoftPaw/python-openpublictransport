"""Tests for telling disruptions apart from routine notices.

EFA mixes routine timetable and vehicle information ("bicycles limited",
"wheelchair accessible vehicle", "on-board restaurant") with real disruptions
in the same ``hints`` list, so everything ended up in a red warning banner.
See https://github.com/NerdySoftPaw/openpublictransport-card/issues/15.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

from openpublictransport.models import UnifiedDeparture
from openpublictransport.providers.gvh import GVHProvider

TZ = ZoneInfo("Europe/Berlin")
NOW = datetime(2026, 10, 16, 20, 0, tzinfo=TZ)


def _stop_event(hints=None, infos=None) -> dict:
    """A GVH departure from Hannover Hbf as the live API returns it."""
    event = {
        "location": {"id": "de:03241:31", "name": "Hauptbahnhof", "properties": {"platform": "8"}},
        "departureTimePlanned": "2026-10-16T18:10:00Z",
        "departureTimeEstimated": "2026-10-16T18:12:00Z",
        "transportation": {
            "name": "ICE 550 InterCityExpress",
            "number": "550",
            "destination": {"name": "Köln Hbf"},
            "product": {"class": 0, "name": "ICE"},
        },
    }
    if hints is not None:
        event["hints"] = hints
    if infos is not None:
        event["infos"] = infos
    return event


def test_routine_hints_are_notices_but_not_disruptions():
    """Timetable and VehicleType hints stay notices without becoming disruptions."""
    event = _stop_event(hints=[
        {"content": "behindertengerechtes Fahrzeug", "providerCode": "13", "type": "VehicleType"},
        {"content": "Bordrestaurant", "providerCode": "BR", "type": "Timetable"},
    ])
    dep = GVHProvider(None).parse_departure(event, TZ, NOW)

    assert dep.notices == ["behindertengerechtes Fahrzeug", "Bordrestaurant"]
    assert dep.disruptions == []
    result = dep.to_dict()
    assert result["notices"] == ["behindertengerechtes Fahrzeug", "Bordrestaurant"]
    assert result["disruptions"] == []


def test_realtime_hints_and_infos_are_disruptions():
    """Real-time incident hints and infos are disruptions; routine hints are not."""
    event = _stop_event(
        hints=[
            {"content": "Bordrestaurant", "providerCode": "BR", "type": "Timetable"},
            {"content": "Technische Störung am Zug", "providerCode": "x", "type": "RTIncidentCall"},
        ],
        infos=[{"type": "lineInfo", "priority": "normal", "title": "Bauarbeiten S1, S2, S5"}],
    )
    dep = GVHProvider(None).parse_departure(event, TZ, NOW)

    assert dep.notices == ["Bauarbeiten S1, S2, S5", "Bordrestaurant", "Technische Störung am Zug"]
    assert dep.disruptions == ["Bauarbeiten S1, S2, S5", "Technische Störung am Zug"]


def test_hint_without_type_is_a_disruption():
    """An unknown or missing hint type is kept as a disruption, never hidden."""
    dep = GVHProvider(None).parse_departure(_stop_event(hints=[{"content": "Ersatzverkehr"}]), TZ, NOW)

    assert dep.disruptions == ["Ersatzverkehr"]


def test_no_notices_no_keys():
    """Without notices, neither key appears in the attributes."""
    dep = GVHProvider(None).parse_departure(_stop_event(), TZ, NOW)

    assert dep.notices is None and dep.disruptions is None
    assert "notices" not in dep.to_dict()
    assert "disruptions" not in dep.to_dict()


def test_unclassified_notices_all_count_as_disruptions():
    """Providers that do not classify their notices report all of them as disruptions."""
    dep = UnifiedDeparture(
        line="U1",
        destination="Somewhere",
        departure_time="20:10",
        planned_time="20:10",
        delay=0,
        platform=None,
        transportation_type="subway",
        is_realtime=True,
        minutes_until_departure=10,
        departure_time_obj=NOW,
        notices=["Cancelled"],
    )

    assert dep.to_dict()["disruptions"] == ["Cancelled"]
