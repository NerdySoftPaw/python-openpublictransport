"""Tests for the mgate/OTP/DB providers added in the provider expansion."""

import hashlib
import json
from unittest.mock import AsyncMock

import pytest

from openpublictransport.providers import get_provider_class
from openpublictransport.providers.db import DBProvider
from openpublictransport.providers.hafas_mgate_base import _salt_bytes
from openpublictransport.providers.nvv import NVVProvider
from openpublictransport.providers.trimet import TriMetProvider
from openpublictransport.providers.vao import VAOProvider
from openpublictransport.providers.vbn_hafas import VBNHafasProvider
from openpublictransport.providers.vos import VOSProvider
from openpublictransport.providers.vsn import VSNProvider

PROVIDERS = {
    "vbn_hafas": (VBNHafasProvider, "Europe/Berlin"),
    "nvv": (NVVProvider, "Europe/Berlin"),
    "vos": (VOSProvider, "Europe/Berlin"),
    "vsn": (VSNProvider, "Europe/Berlin"),
    "vao_at": (VAOProvider, "Europe/Vienna"),
    "trimet_us": (TriMetProvider, "America/Los_Angeles"),
}


@pytest.mark.parametrize("provider_id, cls, tz", [(k, *v) for k, v in PROVIDERS.items()])
def test_registered_with_metadata(provider_id, cls, tz):
    assert get_provider_class(provider_id) is cls
    provider = cls(session=None)
    assert provider.provider_id == provider_id
    assert provider.get_timezone() == tz
    assert provider.requires_api_key is False


# -- mgate request details ------------------------------------------------------

def _mgate_ok():
    return {"svcResL": [{"res": {"match": {"locL": []}}}]}


async def test_ext_is_sent_when_configured():
    nvv = NVVProvider(session=None)
    nvv._request = AsyncMock(return_value=_mgate_ok())
    await nvv.search_stops("Kassel")
    assert json.loads(nvv._request.call_args.kwargs["data"])["ext"] == "NVV.6.0"

    vbn = VBNHafasProvider(session=None)
    vbn._request = AsyncMock(return_value=_mgate_ok())
    await vbn.search_stops("Bremen")
    assert "ext" not in json.loads(vbn._request.call_args.kwargs["data"])


async def test_vsn_request_is_signed_like_hafas_client():
    vsn = VSNProvider(session=None)
    vsn._request = AsyncMock(return_value=_mgate_ok())
    await vsn.search_stops("Göttingen")

    url = vsn._request.call_args.args[1]
    raw = vsn._request.call_args.kwargs["data"]
    mic = hashlib.md5(raw).hexdigest()
    # hafas-client: mac = md5(hex(mic) + Buffer.from(salt, "hex"))
    mac = hashlib.md5(mic.encode() + bytes.fromhex("535033316D4275665379434C6D4E7870")).hexdigest()
    assert url == f"{VSNProvider.mgate_endpoint}?mic={mic}&mac={mac}"


def test_salt_decoding():
    assert _salt_bytes("535033316D4275665379434C6D4E7870") == b"SP31mBufSyCLmNxp"
    assert _salt_bytes("not-hex!") == b"not-hex!"


def test_vao_uses_the_current_gateway_path():
    assert VAOProvider.mgate_endpoint.endswith("/hamm/gate")


@pytest.mark.parametrize("cat_out", ["ECE", "SEV", "Bsv"])
def test_more_german_categories(cat_out):
    assert VSNProvider(session=None)._transport_type({"prodCtx": {"catOut": cat_out}}) != "unknown"


# -- OTP REST (TriMet) -------------------------------------------------------------

async def test_otp_rest_without_alerts_endpoint_and_pattern_route_ids():
    from openpublictransport.exceptions import ApiError

    provider = TriMetProvider(session=None)
    routes = [{"id": "1:90", "longName": "MAX Red Line", "mode": "TRAM", "agencyName": "TriMet"}]
    stoptimes = [{"pattern": {"id": "1:90:1:01", "desc": "MAX Red Line to Airport"},
                  "times": [{"serviceDay": 1792134000, "scheduledDeparture": 40080, "realtimeDeparture": 40140,
                             "departureDelay": 60, "realtime": True, "headsign": "Airport"}]}]

    async def fake_get(url, params=None):
        if url.endswith("/alerts"):
            raise ApiError("TriMet", 500)
        return routes if url.endswith("/routes") else stoptimes

    provider._get = fake_get
    data = await provider.fetch_departures("1:8383", "", "", 5)

    event = data["stopEvents"][0]
    assert event["routeName"] == "MAX Red Line"
    assert event["transportType"] == "tram"


async def test_otp_rest_departure_errors_still_raise():
    from openpublictransport.exceptions import ApiError

    provider = TriMetProvider(session=None)

    async def fake_get(url, params=None):
        if url.endswith("/stoptimes"):
            raise ApiError("TriMet", 503)
        return []

    provider._get = fake_get
    with pytest.raises(ApiError):
        await provider.fetch_departures("1:8383", "", "", 5)


async def test_otp_rest_geocodes_in_the_providers_country():
    provider = TriMetProvider(session=None)
    provider._request = AsyncMock(return_value=[])
    await provider._geocode("Pioneer Courthouse Square")
    assert provider._request.call_args.kwargs["params"]["countrycodes"] == "us"


# -- DB via Transitous -------------------------------------------------------------

async def test_db_resolves_legacy_station_numbers_once():
    provider = DBProvider(session=None)
    provider._request = AsyncMock(side_effect=[
        [{"id": "de-DELFI_de:06412:10", "name": "Frankfurt (Main) Hauptbahnhof", "country": "DE"}],
        {"stopTimes": []},
        {"stopTimes": []},
    ])

    await provider.fetch_departures("8000105", "", "Frankfurt(Main)Hbf", 10)
    await provider.fetch_departures("8000105", "", "Frankfurt(Main)Hbf", 10)

    urls = [call.args[1] for call in provider._request.call_args_list]
    assert "geocode" in urls[0]
    assert all("stopId=de-DELFI_de%3A06412%3A10" in url for url in urls[1:])
    assert len(urls) == 3  # the second update reuses the resolved id


async def test_db_search_keeps_german_stops():
    provider = DBProvider(session=None)
    provider._request = AsyncMock(return_value=[
        {"id": "a", "name": "Basel SBB", "country": "CH"},
        {"id": "b", "name": "Basel Bad Bf", "country": "DE"},
    ])
    assert [stop["id"] for stop in await provider.search_stops("Basel")] == ["b"]
