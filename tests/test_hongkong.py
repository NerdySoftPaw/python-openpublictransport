"""Tests for the Hong Kong (KMB / MTR) provider."""

from datetime import datetime
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest

from openpublictransport.providers.hongkong import (
    MTR_LINE_STATIONS,
    MTR_LINES,
    MTR_STATIONS,
    PROVIDER_HONGKONG,
    HongKongProvider,
)

TZ = ZoneInfo("Asia/Hong_Kong")
NOW = datetime(2026, 10, 17, 2, 0, tzinfo=TZ)


def _kmb(route="N241", eta="2026-10-17T02:09:17+08:00", rmk="", service_type=1):
    return {
        "co": "KMB", "route": route, "dir": "O", "service_type": service_type, "seq": 12,
        "dest_tc": "青衣(長宏邨)", "dest_en": "TSING YI (CHEUNG WANG ESTATE)", "eta_seq": 1, "eta": eta,
        "rmk_tc": "", "rmk_en": rmk, "data_timestamp": "2026-10-17T02:01:09+08:00",
    }


MTR_RESPONSE = {
    "sys_time": "2026-10-17 05:59:23", "curr_time": "2026-10-17 05:59:20",
    "data": {"TWL-TST": {
        "curr_time": "2026-10-17 05:59:20",
        "UP": [{"seq": "1", "dest": "TSW", "plat": "1", "time": "2026-10-17 06:10:00", "ttnt": "10",
                "valid": "Y", "source": "-"}],
        "DOWN": [{"seq": "1", "dest": "CEN", "plat": "2", "time": "2026-10-17 06:04:00", "ttnt": "4",
                  "valid": "Y", "source": "-"}],
    }},
    "isdelay": "N", "status": 1, "message": "successful",
}


def test_provider_metadata():
    p = HongKongProvider(session=None)
    assert p.provider_id == PROVIDER_HONGKONG == "hongkong"
    assert p.provider_name == "Hong Kong (KMB / MTR)"
    assert p.get_timezone() == "Asia/Hong_Kong"
    assert p.requires_api_key is False


def test_static_mtr_table_is_consistent():
    assert set(MTR_LINE_STATIONS) == set(MTR_LINES)
    for stations in MTR_LINE_STATIONS.values():
        assert all(code in MTR_STATIONS for code in stations)
    assert MTR_STATIONS["TST"] == ("Tsim Sha Tsui", "尖沙咀")


def test_parse_kmb():
    dep = HongKongProvider(session=None).parse_departure(dict(_kmb(), operator="kmb"), TZ, NOW)
    assert dep.line == "N241"
    assert dep.destination == "TSING YI (CHEUNG WANG ESTATE)"
    assert dep.departure_time == dep.planned_time == "02:09"
    assert dep.delay == 0
    assert dep.is_realtime is True
    assert dep.transportation_type == "bus"
    assert dep.minutes_until_departure == 9
    assert dep.platform is None


def test_parse_kmb_scheduled_bus_is_not_realtime():
    dep = HongKongProvider(session=None).parse_departure(dict(_kmb(rmk="Scheduled Bus"), operator="kmb"), TZ, NOW)
    assert dep.is_realtime is False
    assert dep.notices == ["Scheduled Bus"]


def test_parse_mtr_resolves_destination_and_type():
    p = HongKongProvider(session=None)
    train = {"seq": "1", "dest": "LOW", "plat": "7", "time": "2026-10-17 06:06:16", "operator": "mtr",
             "line": "EAL", "sta": "ADM", "direction": "UP", "isdelay": False}
    dep = p.parse_departure(train, TZ, NOW)
    assert dep.destination == "Lo Wu"
    assert dep.platform == "7"
    assert dep.departure_time == "06:06"
    assert dep.transportation_type == "train"
    assert dep.description == "East Rail Line"
    assert dep.is_realtime is True
    dep = p.parse_departure(dict(train, line="TWL", dest="TSW"), TZ, NOW)
    assert dep.transportation_type == "subway"
    assert dep.destination == "Tsuen Wan"


def test_parse_invalid_time_is_skipped():
    p = HongKongProvider(session=None)
    assert p.parse_departure({"operator": "mtr", "line": "TWL", "time": "bogus"}, TZ, NOW) is None
    assert p.parse_departure(dict(_kmb(eta=None), operator="kmb"), TZ, NOW) is None


async def test_fetch_kmb_skips_null_eta_and_dedupes_service_types():
    p = HongKongProvider(session=None)
    p._request = AsyncMock(return_value={"type": "StopETA", "data": [
        _kmb(route="N237", eta="2026-10-17T02:30:00+08:00"),
        _kmb(eta=None),
        _kmb(),
        _kmb(service_type=2),
    ]})
    result = await p.fetch_departures("kmb:3856383C8A22904A", "", "", 10)
    events = result["stopEvents"]
    assert [e["route"] for e in events] == ["N241", "N237"]
    assert p._request.call_args.args[1].endswith("/stop-eta/3856383C8A22904A")


async def test_fetch_mtr_line_station():
    p = HongKongProvider(session=None)
    p._request = AsyncMock(return_value=MTR_RESPONSE)
    result = await p.fetch_departures("mtr:TWL:TST", "", "", 10)
    deps = [p.parse_departure(e, TZ, NOW) for e in result["stopEvents"]]
    assert [(d.departure_time, d.destination) for d in deps] == [("06:04", "Central"), ("06:10", "Tsuen Wan")]
    assert p._request.call_args.kwargs["params"] == {"line": "TWL", "sta": "TST"}


async def test_fetch_mtr_station_queries_every_line():
    p = HongKongProvider(session=None)
    p._request = AsyncMock(return_value={"status": 0, "message": "The contents are empty!",
                                         "error": {"errorCode": "NT-204"}})
    result = await p.fetch_departures("mtr:ADM", "", "", 10)
    assert result == {"stopEvents": []}
    lines = {c.kwargs["params"]["line"] for c in p._request.call_args_list}
    assert lines == {"EAL", "ISL", "SIL", "TWL"}


async def test_invalid_stop_id_raises():
    with pytest.raises(ValueError):
        await HongKongProvider(session=None).fetch_departures("ctb:001234", "", "", 5)


async def test_search_mtr_and_kmb_cached():
    p = HongKongProvider(session=None)
    p._request = AsyncMock(return_value={"type": "StopList", "data": [
        {"stop": "A6AED77D365E7011", "name_en": "TAK SHING STREET TSIM SHA TSUI (YT555)", "name_tc": "尖沙咀德成街 (YT555)"},
        {"stop": "18492910339410B1", "name_en": "CHUK YUEN ESTATE BUS TERMINUS (WT916)", "name_tc": "竹園邨總站 (WT916)"},
    ]})
    results = await p.search_stops("tsim sha tsui")
    ids = [r["id"] for r in results]
    assert ids == ["mtr:TWL:TST", "mtr:TML:ETS", "kmb:A6AED77D365E7011"]
    assert results[0]["name"] == "Tsim Sha Tsui (Tsuen Wan Line)"
    assert results[1]["transport_modes"] == ["train"]
    chinese = await p.search_stops("尖沙咀")
    assert [r["id"] for r in chinese] == ["mtr:TWL:TST", "kmb:A6AED77D365E7011"]
    assert p._request.await_count == 1
