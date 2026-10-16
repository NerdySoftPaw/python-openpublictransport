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
