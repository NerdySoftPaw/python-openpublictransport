"""Tests for the CTA (Chicago) Train Tracker / Bus Tracker provider.

Fixtures follow the sample responses in CTA's official API documentation,
and the error payloads were captured from the live APIs with a bogus key.
"""

from datetime import datetime
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest

from openpublictransport.exceptions import ApiError, ApiResponseError, AuthenticationError
from openpublictransport.providers.cta import (
    BUS_API_URL,
    PROVIDER_CTA_US,
    TRAIN_API_URL,
    CTAProvider,
    decode_stop_id,
    encode_stop_id,
)

TZ = ZoneInfo("America/Chicago")
NOW = datetime(2015, 4, 30, 20, 23, 53, tzinfo=TZ)
BUS_NOW = datetime(2025, 4, 21, 16, 4, tzinfo=TZ)


def _eta(**overrides):
    # Train Tracker Arrivals API JSON sample (ttdocs).
    eta = {
        "staId": "40960",
        "stpId": "30185",
        "staNm": "Pulaski",
        "stpDe": "Service toward Loop",
        "rn": "726",
        "rt": "Org",
        "destSt": "30182",
        "destNm": "Loop",
        "trDr": "1",
        "prdt": "2015-04-30T20:23:32",
        "arrT": "2015-04-30T20:25:32",
        "isApp": "0",
        "isSch": "0",
        "isDly": "0",
        "isFlt": "0",
        "flags": None,
        "lat": "41.78661",
        "lon": "-87.73796",
        "heading": "357",
    }
    eta.update(overrides)
    return eta


def _prd(**overrides):
    # Bus Tracker API v3 getpredictions JSON sample (developer guide 2025-04-21).
    prd = {
        "tmstmp": "20250421 16:04",
        "typ": "A",
        "stpnm": "Madison & Jefferson",
        "stpid": "456",
        "vid": "8184",
        "dstp": 686,
        "rt": "20",
        "rtdd": "20",
        "rtdir": "Westbound",
        "des": "Austin",
        "prdtm": "20250421 16:06",
        "tablockid": "20 -803",
        "tatripid": "1040713",
        "origtatripno": "262522629",
        "dly": False,
        "dyn": 0,
        "prdctdn": "DUE",
        "zone": "",
        "stst": 57120,
        "stsd": "2025-04-21",
        "flagstop": 0,
    }
    prd.update(overrides)
    return prd


def _train_ok(*etas):
    return {"ctatt": {"tmst": "2015-04-30T20:23:53", "errCd": "0", "errNm": None, "eta": list(etas)}}


TRAIN_BAD_KEY = {
    "ctatt": {
        "tmst": "2026-10-16T14:03:15",
        "TimeStamp": "2026-10-16T14:03:15",
        "errCd": "101",
        "errNm": "Invalid API key",
    }
}
BUS_BAD_KEY = {"bustime-response": {"error": [{"msg": "Invalid API access key supplied"}]}}

GEOCODE = [
    {"type": "STOP", "id": "us-il-CTA_40380", "name": "Clark/Lake", "modes": ["SUBWAY"]},
    {"type": "STOP", "id": "us-il-Metra_NCHICAGO", "name": "North Chicago", "modes": ["REGIONAL_RAIL"]},
    {"type": "STOP", "id": "us-il-CTA_18222", "name": "Chicago & Clark", "modes": ["BUS"]},
    {"type": "STOP", "id": "us-il-CTA_30374", "name": "Clark/Lake", "modes": ["SUBWAY"]},
    {"type": "STOP", "id": "us-il-CTA_40380", "name": "Clark/Lake", "modes": ["SUBWAY"]},
    {"type": "STOP", "id": "us-il-Pace_215W0020", "name": "Howard St & Chicago Ave", "modes": ["BUS"]},
]


def _provider(**kwargs):
    return CTAProvider(session=None, api_key=kwargs.get("bus", "BUSKEY"), api_key_secondary=kwargs.get("train", "TRAINKEY"))


# --------------------------------------------------------------- metadata / ids


def test_provider_metadata():
    p = CTAProvider(session=None)
    assert p.provider_id == PROVIDER_CTA_US == "cta_us"
    assert p.get_timezone() == "America/Chicago"
    assert p.requires_api_key is True


@pytest.mark.parametrize(
    "raw, expected",
    [("40380", "train:40380"), ("30374", "train:30374"), ("1106", "bus:1106"), ("29999", "bus:29999")],
)
def test_encode_stop_id_uses_cta_ranges(raw, expected):
    assert encode_stop_id(raw) == expected


def test_decode_stop_id_accepts_encoded_bare_and_transitous_ids():
    assert decode_stop_id("train:40380") == ("train", "40380")
    assert decode_stop_id("bus:1106") == ("bus", "1106")
    assert decode_stop_id("40900") == ("train", "40900")
    assert decode_stop_id("us-il-CTA_1106") == ("bus", "1106")


# ------------------------------------------------------------------- parsing


def test_train_live_prediction():
    dep = _provider().parse_departure({**_eta(), "_cta": "train"}, TZ, NOW)
    assert dep.line == "Orange Line"
    assert dep.destination == "Loop"
    assert dep.departure_time == dep.planned_time == "20:25"
    assert dep.delay == 0
    assert dep.transportation_type == "subway"
    assert dep.is_realtime is True
    assert dep.minutes_until_departure == 1
    assert dep.description == "Service toward Loop"
    assert dep.agency == "CTA"
    assert dep.notices is None
    assert dep.line_color == "#F9461C"
    assert dep.departure_time_obj.tzinfo is not None


def test_train_scheduled_prediction_is_not_realtime():
    dep = _provider().parse_departure(_eta(isSch="1"), TZ, NOW)
    assert dep.is_realtime is False


def test_train_delayed_adds_notice():
    dep = _provider().parse_departure(_eta(isDly="1"), TZ, NOW)
    assert dep.notices == ["Delayed"]
    assert dep.delay == 0


@pytest.mark.parametrize(
    "code, name", [("Red", "Red Line"), ("Blue", "Blue Line"), ("Brn", "Brown Line"), ("G", "Green Line"),
                   ("P", "Purple Line"), ("Pink", "Pink Line"), ("Y", "Yellow Line")]
)
def test_train_route_names(code, name):
    assert _provider().parse_departure(_eta(rt=code), TZ, NOW).line == name


def test_train_accepts_documented_compact_time_format():
    dep = _provider().parse_departure(_eta(arrT="20150430 20:25:32"), TZ, NOW)
    assert dep.departure_time == "20:25"


def test_train_time_converted_to_target_timezone():
    dep = _provider().parse_departure(_eta(), ZoneInfo("UTC"), NOW)
    assert dep.departure_time == "01:25"  # CDT is UTC-5


def test_bus_prediction_due():
    dep = _provider().parse_departure({**_prd(), "_cta": "bus"}, TZ, BUS_NOW)
    assert dep.line == "20"
    assert dep.destination == "Austin"
    assert dep.departure_time == dep.planned_time == "16:06"
    assert dep.delay == 0
    assert dep.transportation_type == "bus"
    assert dep.is_realtime is True
    assert dep.minutes_until_departure == 0
    assert dep.description == "Westbound"
    assert dep.notices is None


def test_bus_countdown_minutes():
    dep = _provider().parse_departure({**_prd(prdctdn="7", prdtm="20250421 16:11"), "_cta": "bus"}, TZ, BUS_NOW)
    assert dep.minutes_until_departure == 7


def test_bus_delayed_and_cancelled_notices():
    delayed = _provider().parse_departure({**_prd(dly=True, prdctdn="DLY"), "_cta": "bus"}, TZ, BUS_NOW)
    assert delayed.notices == ["Delayed"]
    cancelled = _provider().parse_departure({**_prd(dyn=1), "_cta": "bus"}, TZ, BUS_NOW)
    assert cancelled.notices == ["Cancelled"]
    new_cancelled = _provider().parse_departure({**_prd(dyn=18), "_cta": "bus"}, TZ, BUS_NOW)
    assert new_cancelled.notices == ["Cancelled"]
    # 12 = "Delayed Cancel": must not be shown to the public as cancelled.
    assert _provider().parse_departure({**_prd(dyn=12), "_cta": "bus"}, TZ, BUS_NOW).notices is None


def test_unparseable_entry_returns_none():
    assert _provider().parse_departure({"_cta": "bus", "prdtm": "garbage"}, TZ, BUS_NOW) is None
    assert _provider().parse_departure({"_cta": "train", "arrT": None}, TZ, NOW) is None


# ------------------------------------------------------------ request building


async def test_train_station_uses_mapid_and_secondary_key():
    p = _provider()
    p._request = AsyncMock(return_value=_train_ok(_eta(), _eta(rt="Red")))
    result = await p.fetch_departures("train:40380", "", "", 5)
    args, kwargs = p._request.call_args
    assert args == ("get", TRAIN_API_URL)
    assert kwargs["params"] == {"key": "TRAINKEY", "mapid": "40380", "max": 5, "outputType": "JSON"}
    assert len(result["stopEvents"]) == 2
    assert all(e["_cta"] == "train" for e in result["stopEvents"])


async def test_train_platform_uses_stpid():
    p = _provider()
    p._request = AsyncMock(return_value=_train_ok())
    result = await p.fetch_departures("train:30374", "", "", 3)
    assert p._request.call_args.kwargs["params"]["stpid"] == "30374"
    assert "mapid" not in p._request.call_args.kwargs["params"]
    assert result == {"stopEvents": []}


async def test_bus_stop_uses_primary_key():
    p = _provider()
    p._request = AsyncMock(return_value={"bustime-response": {"prd": [_prd(), _prd(dyn=16), _prd(rt="X9")]}})
    result = await p.fetch_departures("bus:1106", "", "", 10)
    args, kwargs = p._request.call_args
    assert args == ("get", BUS_API_URL)
    assert kwargs["params"] == {"key": "BUSKEY", "stpid": "1106", "format": "json", "top": 10}
    # dyn=16 (invalidated trip) is dropped.
    assert [e["rt"] for e in result["stopEvents"]] == ["20", "X9"]


async def test_missing_train_key_raises():
    p = _provider(train=None)
    p._request = AsyncMock()
    with pytest.raises(AuthenticationError, match="Train Tracker key is required"):
        await p.fetch_departures("train:40380", "", "", 5)
    p._request.assert_not_called()


async def test_missing_bus_key_raises():
    p = _provider(bus=None)
    p._request = AsyncMock()
    with pytest.raises(AuthenticationError, match="Bus Tracker key is required"):
        await p.fetch_departures("bus:1106", "", "", 5)


async def test_train_invalid_key_payload_raises_auth_error():
    p = _provider()
    p._request = AsyncMock(return_value=TRAIN_BAD_KEY)
    with pytest.raises(AuthenticationError, match="Invalid API key"):
        await p.fetch_departures("train:40380", "", "", 5)


async def test_train_quota_and_other_errors():
    p = _provider()
    p._request = AsyncMock(return_value={"ctatt": {"errCd": "102", "errNm": "Maximum Daily usage exceeded"}})
    with pytest.raises(ApiError) as exc:
        await p.fetch_departures("train:40380", "", "", 5)
    assert exc.value.status == 429 and not isinstance(exc.value, AuthenticationError)
    p._request = AsyncMock(return_value={"ctatt": {"errCd": "103", "errNm": "Invalid mapid"}})
    with pytest.raises(ApiResponseError, match="Invalid mapid"):
        await p.fetch_departures("train:49999", "", "", 5)


async def test_bus_invalid_key_payload_raises_auth_error():
    p = _provider()
    p._request = AsyncMock(return_value=BUS_BAD_KEY)
    with pytest.raises(AuthenticationError, match="Invalid API access key"):
        await p.fetch_departures("bus:1106", "", "", 5)


@pytest.mark.parametrize("msg", ["No arrival times", "No service scheduled"])
async def test_bus_no_arrivals_is_empty(msg):
    p = _provider()
    p._request = AsyncMock(return_value={"bustime-response": {"error": [{"stpid": "1106", "msg": msg}]}})
    assert await p.fetch_departures("bus:1106", "", "", 5) == {"stopEvents": []}


async def test_bus_unknown_error_raises():
    p = _provider()
    p._request = AsyncMock(return_value={"bustime-response": {"error": [{"msg": "Invalid parameter provided"}]}})
    with pytest.raises(ApiResponseError):
        await p.fetch_departures("bus:1106", "", "", 5)


async def test_limit_is_applied():
    p = _provider()
    p._request = AsyncMock(return_value=_train_ok(*[_eta() for _ in range(6)]))
    result = await p.fetch_departures("train:40380", "", "", 2)
    assert len(result["stopEvents"]) == 2


# ------------------------------------------------------------------- search


async def test_search_keeps_only_cta_and_encodes_ids():
    p = CTAProvider(session=None)  # search needs no key
    p._request = AsyncMock(return_value=GEOCODE)
    results = await p.search_stops("Clark/Lake")
    assert [r["id"] for r in results] == ["train:40380", "bus:18222", "train:30374"]
    assert results[0]["name"] == "Clark/Lake"
    assert results[0]["transport_modes"] == ["subway"]
    assert results[1]["transport_modes"] == ["bus"]
    url = p._request.call_args.args[1]
    assert "text=Clark%2FLake" in url and "place=" in url


async def test_search_falls_back_to_chicago_suffix():
    p = CTAProvider(session=None)
    p._request = AsyncMock(side_effect=[[GEOCODE[1]], [GEOCODE[0]]])
    results = await p.search_stops("Clark/Lake")
    assert [r["id"] for r in results] == ["train:40380"]
    assert "Chicago" in p._request.call_args.args[1]


async def test_search_empty_term():
    p = CTAProvider(session=None)
    p._request = AsyncMock()
    assert await p.search_stops("  ") == []
    p._request.assert_not_called()


async def test_fetch_without_station_id_searches_first():
    p = _provider()
    p._request = AsyncMock(side_effect=[GEOCODE, _train_ok(_eta())])
    result = await p.fetch_departures(None, "Chicago", "Clark/Lake", 5)
    assert p._request.call_args.kwargs["params"]["mapid"] == "40380"
    assert len(result["stopEvents"]) == 1
