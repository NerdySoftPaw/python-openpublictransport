"""Tests for the NextBus base provider and the TTC (Toronto) provider."""

from datetime import datetime
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest

from openpublictransport.exceptions import ApiResponseError
from openpublictransport.providers.nextbus_base import NextBusBaseProvider, _as_list
from openpublictransport.providers.ttc import PROVIDER_TTC_CA, TTCProvider

TZ = ZoneInfo("America/Toronto")
NOW = datetime(2026, 10, 16, 14, 0, tzinfo=TZ)
EPOCH_1405 = int(datetime(2026, 10, 16, 14, 5, tzinfo=TZ).timestamp() * 1000)


def _pred(epoch=EPOCH_1405, layover="false"):
    return {
        "epochTime": str(epoch),
        "minutes": "5",
        "seconds": "300",
        "isDeparture": "false",
        "affectedByLayover": layover,
        "dirTag": "504_1_504A",
        "branch": "504A",
        "vehicle": "4401",
    }


def test_provider_metadata():
    p = TTCProvider(session=None)
    assert p.provider_id == PROVIDER_TTC_CA == "ttc_ca"
    assert p.provider_name == "TTC (Toronto)"
    assert p.get_timezone() == "America/Toronto"
    assert p.requires_api_key is False
    assert p.agency == "ttc"
    assert isinstance(p, NextBusBaseProvider)


def test_transport_type_mapping():
    p = TTCProvider(session=None)
    assert p.transport_type_for_route("504") == "tram"
    assert p.transport_type_for_route("510") == "tram"
    assert p.transport_type_for_route("304") == "tram"  # Blue Night streetcar
    assert p.transport_type_for_route("300") == "bus"  # Blue Night bus
    assert p.transport_type_for_route("29") == "bus"
    assert p.transport_type_for_route("900") == "bus"


def test_as_list_handles_object_or_list():
    assert _as_list(None) == []
    assert _as_list({"a": 1}) == [{"a": 1}]
    assert _as_list([1, 2]) == [1, 2]


def test_flatten_single_objects():
    """One route, one direction, one prediction: everything is an object."""
    data = {
        "predictions": {
            "routeTag": "504",
            "routeTitle": "504-King",
            "stopTitle": "King St West At Spadina Ave",
            "agencyTitle": "Toronto Transit Commission",
            "message": {"text": "Detour in effect", "priority": "Normal"},
            "direction": {"title": "East - 504a King towards Distillery", "prediction": _pred()},
        }
    }
    events = NextBusBaseProvider.flatten_predictions(data)
    assert len(events) == 1
    assert events[0]["routeTag"] == "504"
    assert events[0]["notices"] == ["Detour in effect"]


def test_flatten_lists_and_skips_routes_without_predictions():
    data = {
        "predictions": [
            {"routeTag": "310", "routeTitle": "310-Spadina", "dirTitleBecauseNoPredictions": "South - 310"},
            {
                "routeTag": "510",
                "routeTitle": "510-Spadina",
                "direction": [
                    {"title": "South - 510 Spadina towards Union Station", "prediction": [_pred(), _pred()]},
                    {"title": "North - 510 Spadina towards Spadina Station", "prediction": _pred()},
                ],
            },
        ]
    }
    events = NextBusBaseProvider.flatten_predictions(data)
    assert len(events) == 3
    assert {e["routeTag"] for e in events} == {"510"}


def test_parse_departure():
    p = TTCProvider(session=None)
    event = NextBusBaseProvider.flatten_predictions(
        {"predictions": {"routeTag": "504", "routeTitle": "504-King",
                         "direction": {"title": "East - 504a King towards Distillery", "prediction": _pred()}}}
    )[0]
    dep = p.parse_departure(event, TZ, NOW)
    assert dep.line == "504"
    assert dep.destination == "Distillery"
    assert dep.departure_time == dep.planned_time == "14:05"
    assert dep.delay == 0
    assert dep.is_realtime is True
    assert dep.minutes_until_departure == 5
    assert dep.transportation_type == "tram"
    assert dep.description == "504-King"
    assert dep.notices is None


def test_parse_layover_notice_and_missing_epoch():
    p = TTCProvider(session=None)
    event = NextBusBaseProvider.flatten_predictions(
        {"predictions": {"routeTag": "29", "direction": {"title": "North", "prediction": _pred(layover="true")}}}
    )[0]
    dep = p.parse_departure(event, TZ, NOW)
    assert dep.transportation_type == "bus"
    assert dep.destination == "North"
    assert dep.notices == ["Estimate affected by layover"]
    assert p.parse_departure({"routeTag": "29"}, TZ, NOW) is None


async def test_fetch_departures_sorts_and_limits():
    p = TTCProvider(session=None)
    later = EPOCH_1405 + 600_000
    p._request = AsyncMock(return_value={
        "predictions": {"routeTag": "504", "direction": {"title": "x towards Y",
                                                         "prediction": [_pred(later), _pred(EPOCH_1405)]}}
    })
    result = await p.fetch_departures("8134", "", "", 1)
    assert [e["epochTime"] for e in result["stopEvents"]] == [EPOCH_1405]
    params = p._request.call_args.kwargs["params"]
    assert params == {"command": "predictions", "a": "ttc", "stopId": "8134"}


async def test_error_payload_raises():
    p = TTCProvider(session=None)
    p._request = AsyncMock(return_value={"Error": {"shouldRetry": "false", "content": "stopId=1 is not valid"}})
    with pytest.raises(ApiResponseError, match="not valid"):
        await p.fetch_departures("1", "", "", 5)


async def test_search_builds_index_once_and_filters_locally():
    p = TTCProvider(session=None)

    async def fake_request(method, url, params=None, **kwargs):
        if params["command"] == "routeList":
            return {"route": [{"tag": "504", "title": "504-King"}, {"tag": "29", "title": "29-Dufferin"}]}
        if params["r"] == "504":
            assert "terse" in params
            return {"route": {"stop": [
                {"tag": "1", "stopId": "15648", "title": "King St West At Spadina Ave East Side"},
                {"tag": "1_ar", "title": "Distillery Loop"},
            ]}}
        return {"route": {"stop": {"tag": "2", "stopId": "15648", "title": "King St West At Spadina Ave East Side"}}}

    p._request = AsyncMock(side_effect=fake_request)
    results = await p.search_stops("king spadina")
    assert results == [{
        "id": "15648", "name": "King St West At Spadina Ave East Side", "place": "Toronto",
        "area_type": "stop", "transport_modes": ["bus", "tram"],
    }]
    calls = p._request.await_count
    assert await p.search_stops("Distillery") == []  # arrival-only stop without stopId is not indexed
    assert await p.search_stops("KING st west") != []
    assert p._request.await_count == calls  # cached
