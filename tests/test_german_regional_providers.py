"""Tests for the German regional providers VMV, GVH, NAH.SH, INSA, VMT and saarVV."""

import json
from datetime import datetime
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest

from openpublictransport.providers import get_provider_class
from openpublictransport.providers.gvh import GVHProvider
from openpublictransport.providers.insa import INSAProvider
from openpublictransport.providers.nahsh import NAHSHProvider
from openpublictransport.providers.saarvv import SaarVVProvider
from openpublictransport.providers.vmt import VMTProvider
from openpublictransport.providers.vmv import VMVProvider

TZ = ZoneInfo("Europe/Berlin")
NOW = datetime(2026, 10, 16, 18, 0, tzinfo=TZ)

EFA = {"vmv": VMVProvider, "gvh": GVHProvider}
MGATE = {"nahsh": NAHSHProvider, "insa": INSAProvider, "vmt": VMTProvider, "saarvv": SaarVVProvider}


@pytest.mark.parametrize("provider_id, cls", {**EFA, **MGATE}.items())
def test_registered_with_metadata(provider_id, cls):
    assert get_provider_class(provider_id) is cls
    provider = cls(session=None)
    assert provider.provider_id == provider_id
    assert provider.provider_name
    assert provider.get_timezone() == "Europe/Berlin"
    assert provider.requires_api_key is False


# -- EFA ----------------------------------------------------------------------

@pytest.mark.parametrize("cls", EFA.values())
def test_efa_urls_share_one_server(cls):
    provider = cls(session=None)
    assert provider.dm_base_url.endswith("/XML_DM_REQUEST")
    assert provider.sf_base_url.endswith("/XML_STOPFINDER_REQUEST")
    assert provider.dm_base_url.rsplit("/", 1)[0] == provider.sf_base_url.rsplit("/", 1)[0]
    assert provider.dm_base_url.startswith("https://")


def _efa_event(product_class, realtime_status=("MONITORED",)):
    return {
        "departureTimePlanned": "2026-10-16T16:10:00Z",
        "departureTimeEstimated": "2026-10-16T16:10:00Z",
        "realtimeStatus": list(realtime_status),
        "location": {"properties": {"platform": "2"}},
        "transportation": {
            "number": "5",
            "disassembledName": "5",
            "destination": {"name": "Rostock, Mecklenburger Allee"},
            "product": {"class": product_class, "name": "Straßenbahn"},
        },
    }


@pytest.mark.parametrize("cls", EFA.values())
def test_efa_monitored_on_time_departure_counts_as_realtime(cls):
    dep = cls(session=None).parse_departure(_efa_event(4), TZ, NOW)
    assert dep is not None
    assert dep.transportation_type == "tram"
    assert dep.is_realtime is True
    assert dep.delay == 0


@pytest.mark.parametrize("cls", EFA.values())
def test_efa_unmonitored_departure_is_not_realtime(cls):
    dep = cls(session=None).parse_departure(_efa_event(5, realtime_status=()), TZ, NOW)
    assert dep is not None
    assert dep.transportation_type == "bus"
    assert dep.is_realtime is False


# -- HAFAS mgate --------------------------------------------------------------

@pytest.mark.parametrize("cls", MGATE.values())
async def test_mgate_request_carries_operator_config(cls):
    provider = cls(session=None)
    provider._request = AsyncMock(return_value={"svcResL": [{"res": {"match": {"locL": []}}}]})

    assert await provider.search_stops("Hbf") == []

    method, url = provider._request.call_args.args
    body = json.loads(provider._request.call_args.kwargs["data"])
    assert method == "post"
    assert url == cls.mgate_endpoint
    assert body["auth"] == cls.mgate_auth
    assert body["client"] == cls.mgate_client
    assert body["ver"] == cls.mgate_ver
    assert body["lang"] == "de"


def _mgate_event(cat_out, planned="181000", actual="181300"):
    stop = {"dTimeS": planned, "dPltfS": {"txt": "3"}}
    if actual:
        stop["dTimeR"] = actual
    return {
        "date": "20261016",
        "dirTxt": "Kiel, Hauptbahnhof",
        "stbStop": stop,
        "_prod": {"name": f"{cat_out} 11", "prodCtx": {"catOut": cat_out}},
    }


@pytest.mark.parametrize("cls", MGATE.values())
@pytest.mark.parametrize("cat_out, expected", [("Bus", "bus"), ("STR", "tram"), ("RE", "train")])
def test_mgate_parses_german_categories_with_delay(cls, cat_out, expected):
    dep = cls(session=None).parse_departure(_mgate_event(cat_out), TZ, NOW)
    assert dep is not None
    assert dep.transportation_type == expected
    assert dep.delay == 3
    assert dep.is_realtime is True
    assert dep.destination == "Kiel, Hauptbahnhof"


@pytest.mark.parametrize("cls", MGATE.values())
def test_mgate_without_realtime_time_is_scheduled(cls):
    dep = cls(session=None).parse_departure(_mgate_event("Bus", actual=None), TZ, NOW)
    assert dep is not None
    assert dep.is_realtime is False
    assert dep.delay == 0


# -- HAFAS mgate trip search --------------------------------------------------

def _trip_search_response():
    return {"svcResL": [{"res": {
        "common": {
            "locL": [{"name": "Erfurt Hbf"}, {"name": "Jena West"}, {"name": "Jena, Westbahnhof"}, {"name": "Jena Paradies"}],
            "prodL": [
                {"name": "RE1", "prodCtx": {"catOut": "Zug", "catOutL": "Zug"}},
                {"name": "5", "prodCtx": {"catOut": "Strab", "catOutL": "Straßenbahn"}},
            ],
        },
        "outConL": [{
            "date": "20261016", "dur": "005300", "chg": 1,
            "secL": [
                {"type": "JNY", "dep": {"locX": 0, "dTimeS": "195200", "dTimeR": "200800", "dPltfS": {"txt": "4"}},
                 "arr": {"locX": 1, "aTimeS": "201700", "aTimeR": "203300"}, "jny": {"prodX": 0, "dirTxt": "Göttingen"}},
                {"type": "WALK", "dep": {"locX": 1, "dTimeS": "203300"}, "arr": {"locX": 2, "aTimeS": "203700"}},
                {"type": "JNY", "dep": {"locX": 2, "dTimeS": "203900"}, "arr": {"locX": 3, "aTimeS": "204500"},
                 "jny": {"prodX": 1, "dirTxt": "Lobeda", "isCncl": True}},
            ],
        }, {
            "date": "20261016", "dur": "010000", "chg": 0,
            "secL": [{"type": "WALK", "dep": {"locX": 0, "dTimeS": "200000"}, "arr": {"locX": 3, "aTimeS": "210000"}}],
        }],
    }}]}


async def test_search_trips_builds_request_and_parses_connections():
    provider = VMTProvider(session=None)
    provider._request = AsyncMock(return_value=_trip_search_response())

    trips = await provider.search_trips(
        "A=1@L=1@", "A=1@L=4@", departure_time=datetime(2026, 10, 16, 17, 50, tzinfo=ZoneInfo("UTC"))
    )

    req = json.loads(provider._request.call_args.kwargs["data"])["svcReqL"][0]
    assert req["meth"] == "TripSearch"
    assert req["req"]["depLocL"] == [{"lid": "A=1@L=1@"}]
    assert req["req"]["arrLocL"] == [{"lid": "A=1@L=4@"}]
    # 17:50 UTC is 19:50 in Berlin
    assert (req["req"]["outDate"], req["req"]["outTime"]) == ("20261016", "195000")

    # The walk-only connection is dropped
    assert len(trips) == 1
    trip = trips[0]
    assert trip["duration_minutes"] == 53
    assert trip["transfers"] == 1

    train, walk, tram = trip["legs"]
    assert (train["mode"], train["line"], train["transport_type"]) == ("transit", "RE1", "train")
    assert (train["origin"], train["destination"], train["platform"]) == ("Erfurt Hbf", "Jena West", "4")
    assert train["departure_planned"] == datetime(2026, 10, 16, 19, 52, tzinfo=TZ)
    assert train["departure"] == datetime(2026, 10, 16, 20, 8, tzinfo=TZ)
    assert train["direction"] == "Göttingen"
    assert walk["mode"] == "walk"
    assert walk["transport_type"] == "walk"
    assert tram["transport_type"] == "tram"
    assert tram["cancelled"] is True
    assert tram["departure"] == tram["departure_planned"]


async def test_search_trips_with_extid_and_empty_result():
    provider = SaarVVProvider(session=None)
    provider._request = AsyncMock(return_value={"svcResL": [{"res": {}}]})

    assert await provider.search_trips("8000323", "8005265") == []
    req = json.loads(provider._request.call_args.kwargs["data"])["svcReqL"][0]["req"]
    assert req["depLocL"] == [{"type": "S", "extId": "8000323"}]


@pytest.mark.parametrize("cat_out, cat_out_l, expected", [
    ("Strab", "Straßenbahn", "tram"), ("Strb", "Strb", "tram"), ("Zug", "Zug", "train"),
    ("DRE", "Regional-Express", "train"), ("Xyz", "Regionalbus", "bus"), ("Q", "Expressbus", "bus"),
])
def test_german_category_labels(cat_out, cat_out_l, expected):
    prod = {"prodCtx": {"catOut": cat_out, "catOutL": cat_out_l}}
    assert VMTProvider(session=None)._transport_type(prod) == expected


async def test_search_trips_arrive_by():
    provider = NAHSHProvider(session=None)
    provider._request = AsyncMock(return_value={"svcResL": [{"res": {}}]})

    await provider.search_trips("A=1@L=1@", "A=1@L=2@", arrival_time=datetime(2026, 10, 17, 6, 0, tzinfo=ZoneInfo("UTC")))

    req = json.loads(provider._request.call_args.kwargs["data"])["svcReqL"][0]["req"]
    assert req["outFrwd"] is False
    # 06:00 UTC is 08:00 in Berlin
    assert (req["outDate"], req["outTime"]) == ("20261017", "080000")


async def test_search_trips_departure_stays_forward():
    provider = NAHSHProvider(session=None)
    provider._request = AsyncMock(return_value={"svcResL": [{"res": {}}]})
    await provider.search_trips("A=1@L=1@", "A=1@L=2@")
    assert json.loads(provider._request.call_args.kwargs["data"])["svcReqL"][0]["req"]["outFrwd"] is True
