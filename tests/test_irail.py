"""Tests for the iRail (SNCB/NMBS) provider."""

from datetime import datetime
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

from openpublictransport.providers.irail import PROVIDER_ID, USER_AGENT, IRailProvider

TZ = ZoneInfo("Europe/Brussels")
NOW = datetime(2026, 10, 16, 19, 0, tzinfo=TZ)
# 2026-10-16 19:10 Brussels (CEST, UTC+2) == 17:10 UTC
EPOCH_1910 = str(int(datetime(2026, 10, 16, 19, 10, tzinfo=TZ).timestamp()))

STATIONS = {
    "station": [
        {"id": "BE.NMBS.008813003", "name": "Brussels-Central", "standardname": "Brussel-Centraal/Bruxelles-Central"},
        {"id": "BE.NMBS.008892007", "name": "Ghent-Sint-Pieters", "standardname": "Gent-Sint-Pieters"},
        {"id": "BE.NMBS.008814001", "name": "Brussels-South/Brussels-Midi", "standardname": "Brussel-Zuid/Bruxelles-Midi"},
        {"id": "BE.NMBS.008821006", "name": "Antwerp-Central", "standardname": "Antwerpen-Centraal"},
        {"id": "BE.NMBS.008400319", "name": "'s Hertogenbosch", "standardname": "'s Hertogenbosch"},
    ]
}


def _event(**overrides):
    event = {
        "station": "Dendermonde",
        "stationinfo": {"name": "Dendermonde"},
        "time": EPOCH_1910,
        "delay": "0",
        "canceled": "0",
        "vehicleinfo": {"shortname": "IC 2335", "type": "IC"},
        "platform": "3",
        "platforminfo": {"name": "3", "normal": "1"},
    }
    event.update(overrides)
    return event


def test_provider_metadata():
    p = IRailProvider(session=None)
    assert p.provider_id == PROVIDER_ID == "irail_be"
    assert p.get_timezone() == "Europe/Brussels"
    assert p.requires_api_key is False


def test_on_time_departure():
    dep = IRailProvider(session=None).parse_departure(_event(), TZ, NOW)
    assert dep.line == "IC 2335"
    assert dep.destination == "Dendermonde"
    assert dep.planned_time == dep.departure_time == "19:10"
    assert dep.delay == 0
    assert dep.platform == "3"
    assert dep.transportation_type == "train"
    assert dep.is_realtime is True
    assert dep.minutes_until_departure == 10
    assert dep.notices is None
    assert dep.departure_time_obj.tzinfo is not None


def test_delay_in_seconds_shifts_actual_time():
    dep = IRailProvider(session=None).parse_departure(_event(delay="540"), TZ, NOW)
    assert dep.planned_time == "19:10"
    assert dep.departure_time == "19:19"
    assert dep.delay == 9
    assert dep.minutes_until_departure == 19


def test_time_is_converted_to_requested_timezone():
    utc = ZoneInfo("UTC")
    dep = IRailProvider(session=None).parse_departure(_event(), utc, NOW)
    assert dep.planned_time == "17:10"


def test_cancelled_and_platform_change():
    dep = IRailProvider(session=None).parse_departure(
        _event(canceled="1", platform="5", platforminfo={"name": "5", "normal": "0"}), TZ, NOW
    )
    assert dep.notices == ["Cancelled"]
    assert dep.platform == "5"
    assert dep.platform_changed is True


def test_missing_delay_is_not_realtime():
    event = _event()
    del event["delay"]
    assert IRailProvider(session=None).parse_departure(event, TZ, NOW).is_realtime is False


def test_missing_time_is_skipped():
    assert IRailProvider(session=None).parse_departure(_event(time=None), TZ, NOW) is None


async def test_search_matches_all_language_variants_and_caches():
    p = IRailProvider(session=None)
    p._request = AsyncMock(return_value=STATIONS)

    fr = await p.search_stops("bruxelles central")
    assert fr[0]["id"] == "BE.NMBS.008813003"
    en = await p.search_stops("Brussels-Central")
    assert en[0] == {"id": "BE.NMBS.008813003", "name": "Brussels-Central", "place": "", "area_type": "stop"}
    nl = await p.search_stops("Gent-Sint-Pieters")
    assert nl[0]["id"] == "BE.NMBS.008892007"
    assert p._request.await_count == 1
    kwargs = p._request.await_args.kwargs
    assert kwargs["headers"]["User-Agent"] == USER_AGENT


async def test_search_prefers_prefix_matches():
    p = IRailProvider(session=None)
    p._request = AsyncMock(return_value=STATIONS)
    results = await p.search_stops("brus")
    assert [r["id"] for r in results][:2] == ["BE.NMBS.008813003", "BE.NMBS.008814001"]
    assert all("Antwerp" not in r["name"] for r in results)


async def test_search_is_accent_insensitive():
    p = IRailProvider(session=None)
    p._request = AsyncMock(return_value={"station": [{"id": "BE.NMBS.1", "name": "Liège-Guillemins", "standardname": "Liège-Guillemins"}]})
    assert (await p.search_stops("liege"))[0]["id"] == "BE.NMBS.1"


async def test_fetch_departures_builds_request_and_limits():
    p = IRailProvider(session=None)
    p._request = AsyncMock(return_value={"departures": {"number": "3", "departure": [_event(), _event(), _event()]}})
    result = await p.fetch_departures("BE.NMBS.008813003", "", "", 2)
    assert len(result["stopEvents"]) == 2
    args, kwargs = p._request.await_args
    assert args == ("get", "https://api.irail.be/v1/liveboard/")
    assert kwargs["params"] == {"id": "BE.NMBS.008813003", "format": "json", "lang": "en", "arrdep": "departure"}
    assert kwargs["headers"]["User-Agent"] == USER_AGENT
