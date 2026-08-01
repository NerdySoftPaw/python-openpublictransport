"""Tests for EFA platform/track extraction.

EFA's RapidJSON keeps the track under ``location.properties.platform``, but the
base provider used to read ``platform.name`` / ``platformName``, which that
response shape never has — so every EFA provider without its own override
reported an empty platform.
See https://github.com/NerdySoftPaw/openpublictransport/issues/56.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

from openpublictransport.parsers import normalize_platform
from openpublictransport.providers.hvv import HVVProvider
from openpublictransport.providers.kvv import KVVProvider
from openpublictransport.providers.vrr import VRRProvider
from openpublictransport.providers.vvs import VVSProvider

TZ = ZoneInfo("Europe/Berlin")
NOW = datetime(2026, 7, 18, 20, 8, tzinfo=TZ)


# The exact stop event pasted into issue #56 (VVS Vaihingen, S1 to Plochingen).
VVS_STOP_EVENT = {
    "location": {
        "id": "de:08111:6002:1:3",
        "isGlobalId": True,
        "name": "Vaihingen",
        "disassembledName": "Gleis 3",
        "type": "platform",
        "pointType": "TRACK",
        "properties": {
            "stopId": "5006002",
            "platform": "3",
            "platformName": "Gleis 3",
            "plannedPlatformName": "Gleis 3",
        },
    },
    "departureTimePlanned": "2026-07-18T18:25:00Z",
    "departureTimeEstimated": "2026-07-18T18:26:00Z",
    "isRealtimeControlled": True,
    "transportation": {
        "name": "S-Bahn S1",
        "number": "S1",
        "description": "Herrenberg - Stuttgart - Plochingen - Kirchheim (T)",
        "destination": {"name": "Plochingen"},
        "product": {"class": 1, "name": "S-Bahn"},
    },
}


def _bus_stop_event() -> dict:
    """A bus departure: technical platform present, readable label empty."""
    return {
        "location": {
            "id": "de:08111:6002:0:A",
            "name": "Vaihingen",
            "disassembledName": "",
            "type": "platform",
            "pointType": "BUS_POINT",
            "properties": {"stopId": "5006002", "platform": "A"},
        },
        "departureTimePlanned": "2026-07-18T18:25:00Z",
        "departureTimeEstimated": "2026-07-18T18:25:00Z",
        "transportation": {
            "number": "92",
            "destination": {"name": "Rohr"},
            "product": {"class": 5, "name": "Stadtbus"},
        },
    }


def test_vvs_maps_technical_platform():
    """The platform attribute is the technical value from properties.platform."""
    departure = VVSProvider(None).parse_departure(VVS_STOP_EVENT, TZ, NOW)

    assert departure is not None
    assert departure.platform == "3"
    assert departure.line == "S1"
    assert departure.destination == "Plochingen"


def test_vvs_exposes_readable_platform_name_separately():
    """The human-readable label is kept, but not as `platform`."""
    departure = VVSProvider(None).parse_departure(VVS_STOP_EVENT, TZ, NOW)

    assert departure.platform_name == "Gleis 3"
    assert departure.to_dict()["platform"] == "3"
    assert departure.to_dict()["platform_name"] == "Gleis 3"


def test_planned_platform_name_is_not_a_platform_change():
    """"3" vs "Gleis 3" is the same platform, not a real-time change."""
    departure = VVSProvider(None).parse_departure(VVS_STOP_EVENT, TZ, NOW)

    assert departure.platform_changed is False
    assert departure.planned_platform is None
    assert "planned_platform" not in departure.to_dict()


def test_real_platform_change_is_detected():
    """A genuinely different scheduled track still reports a change."""
    event = {
        **VVS_STOP_EVENT,
        "location": {
            **VVS_STOP_EVENT["location"],
            "properties": {
                **VVS_STOP_EVENT["location"]["properties"],
                "platform": "5",
                "platformName": "Gleis 5",
            },
        },
    }

    departure = VVSProvider(None).parse_departure(event, TZ, NOW)

    assert departure.platform == "5"
    assert departure.platform_changed is True
    assert departure.planned_platform == "Gleis 3"
    assert departure.to_dict()["platform_changed"] is True


def test_bus_stop_keeps_technical_platform_without_readable_label():
    """Bus stops have no "Gleis N" label but do have the technical value."""
    departure = VVSProvider(None).parse_departure(_bus_stop_event(), TZ, NOW)

    assert departure.platform == "A"
    assert departure.platform_name is None
    assert "platform_name" not in departure.to_dict()


def test_all_efa_providers_share_the_fix():
    """The fix lives in EFABaseProvider, so it is not VVS-specific."""
    for provider in (VVSProvider(None), VRRProvider(None), KVVProvider(None), HVVProvider(None)):
        departure = provider.parse_departure(VVS_STOP_EVENT, TZ, NOW)
        assert departure is not None, provider.provider_id
        assert departure.platform == "3", provider.provider_id


def test_legacy_platform_shape_still_works():
    """Pre-RapidJSON responses that use platform.name keep working."""
    event = {
        "departureTimePlanned": "2026-07-18T18:25:00Z",
        "departureTimeEstimated": "2026-07-18T18:25:00Z",
        "platform": {"name": "2"},
        "transportation": {
            "number": "U79",
            "destination": {"name": "Duisburg Hbf"},
            "product": {"class": 2, "name": "U-Bahn"},
        },
    }

    departure = VRRProvider(None).parse_departure(event, TZ, NOW)

    assert departure.platform == "2"


def test_missing_platform_is_empty_not_an_error():
    event = {
        "departureTimePlanned": "2026-07-18T18:25:00Z",
        "departureTimeEstimated": "2026-07-18T18:25:00Z",
        "location": {"properties": None},
        "transportation": {
            "number": "92",
            "destination": {"name": "Rohr"},
            "product": {"class": 5, "name": "Stadtbus"},
        },
    }

    departure = VVSProvider(None).parse_departure(event, TZ, NOW)

    assert departure.platform == ""
    assert departure.platform_changed is False


def test_normalize_platform():
    assert normalize_platform("Gleis 3") == "3"
    assert normalize_platform("gleis 3") == "3"
    assert normalize_platform("3") == "3"
    assert normalize_platform(" Bstg. 12 ") == "12"
    assert normalize_platform("Steig B") == "b"
    assert normalize_platform("Platform 4") == "4"
    assert normalize_platform("Track 1") == "1"
    assert normalize_platform(None) == ""
    assert normalize_platform("") == ""
    # A platform genuinely named "Gleis" keeps a usable value
    assert normalize_platform("Gleis") == ""
    assert normalize_platform(3) == "3"
