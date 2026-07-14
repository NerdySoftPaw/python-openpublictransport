"""Tests for the legacy HAFAS "Scotty" base provider and the ÖBB subclass.

Covers the migration away from the suspended ``oebb.macistry.com`` FPTF REST
backend to ÖBB's own Scotty endpoints (``fahrplan.oebb.at``).
See https://github.com/NerdySoftPaw/openpublictransport/issues/50.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

import openpublictransport.providers.hafas_base as hb
from openpublictransport.providers.hafas_base import HafasBaseProvider, _parse_delay
from openpublictransport.providers.oebb import OeBBProvider


# A realistic ``ajax-getstop.exe/dn`` JSONP response: a station (type 1) plus a
# non-station suggestion (type 2, an address) that must be filtered out.
SAMPLE_STOPFINDER = (
    'SLs.sls={"suggestions":['
    '{"value":"Wien Hbf (U)","id":"A=1@O=Wien Hbf (U)@L=001290401@","extId":"001290401",'
    '"type":"1","typeStr":"[Bhf/Hst]","xcoord":"16377950","ycoord":"48184986"},'
    '{"value":"Wien, Some Street 1","id":"A=2@O=Wien@","extId":"","type":"2","typeStr":"[Adr]"}'
    ']};SLs.showSuggestion();'
)

# A realistic ``stboard.exe`` XML board: a delayed train, a cancelled train with
# an HIM message, a U-Bahn with no real-time prognosis, and a tram.
SAMPLE_BOARD = """<?xml version="1.0" encoding="UTF-8"?>
<StationTable>
<Journey fpTime="18:53" fpDate="14.07.2026" delay="+ 60" platform="7A-B" targetLoc="Villach Hbf" prod="RJX 255#RJX" class="1" dir="Villach Hbf" hafasname="RJX 255" />
<Journey fpTime="18:38" fpDate="14.07.2026" delay="cancel" platform="9A-B" targetLoc="Flughafen Wien" prod="RJX 13477#RJX" class="1" dir="Flughafen Wien" hafasname="RJX 13477"><HIMMessage header="Partial cancellation" lead="Due to a delay this train cannot run." display="2" /></Journey>
<Journey fpTime="18:50" fpDate="14.07.2026" delay="-" platform="1" targetLoc="Wien Leopoldau" prod="U1#U" class="256" dir="Wien Leopoldau (U1)" hafasname="U1" />
<Journey fpTime="19:03" fpDate="14.07.2026" delay="0" targetLoc="Raxstra&#223;e" prod="Tram O#Tram" class="512" dir="Raxstra&#223;e" hafasname="Tram O" />
</StationTable>"""

SAMPLE_ERR = '<?xml version="1.0" encoding="UTF-8"?><Err code="H730" text="Your input is not valid." level="E"/>'

TZ = ZoneInfo("Europe/Vienna")
NOW = datetime(2026, 7, 14, 18, 0, tzinfo=TZ)


# -- delay parsing ----------------------------------------------------------

def test_parse_delay_variants():
    assert _parse_delay("+ 60") == (60, True, False)
    assert _parse_delay("0") == (0, True, False)
    assert _parse_delay("-3") == (-3, True, False)
    assert _parse_delay("-") == (0, False, False)      # no prognosis
    assert _parse_delay("") == (0, False, False)
    assert _parse_delay("cancel") == (0, True, True)   # cancelled


# -- stop finder ------------------------------------------------------------

def test_parse_stopfinder_returns_only_stations():
    results = HafasBaseProvider._parse_stopfinder(SAMPLE_STOPFINDER)
    assert results == [
        {"id": "001290401", "name": "Wien Hbf (U)", "place": "", "area_type": "stop"}
    ]


def test_parse_stopfinder_handles_garbage():
    assert HafasBaseProvider._parse_stopfinder("not jsonp at all") == []
    assert HafasBaseProvider._parse_stopfinder("SLs.sls=broken;SLs.showSuggestion") == []


# -- board parsing ----------------------------------------------------------

def _provider():
    return OeBBProvider(session=None)


def test_parse_board_extracts_all_journeys():
    events = _provider()._parse_board(SAMPLE_BOARD.encode("utf-8"))
    assert len(events) == 4
    assert events[0]["prod"] == "RJX 255#RJX"
    # HIM message is surfaced as a notice on the cancelled train
    assert events[1]["notices"] == ["Partial cancellation"]


def test_parse_board_handles_error_document():
    assert _provider()._parse_board(SAMPLE_ERR.encode("utf-8")) == []


def test_parse_board_handles_invalid_xml():
    assert _provider()._parse_board(b"<not-xml") == []


# -- full departure mapping -------------------------------------------------

def test_parse_departure_delayed_train():
    p = _provider()
    events = p._parse_board(SAMPLE_BOARD.encode("utf-8"))
    dep = p.parse_departure(events[0], TZ, NOW)
    assert dep is not None
    assert dep.transportation_type == "train"
    assert dep.line == "RJX 255"
    assert dep.destination == "Villach Hbf"
    assert dep.planned_time == "18:53"
    assert dep.departure_time == "19:53"     # +60 min
    assert dep.delay == 60
    assert dep.is_realtime is True
    assert dep.platform == "7A-B"


def test_parse_departure_cancelled_adds_notice():
    p = _provider()
    events = p._parse_board(SAMPLE_BOARD.encode("utf-8"))
    dep = p.parse_departure(events[1], TZ, NOW)
    assert dep is not None
    assert dep.is_realtime is True
    assert dep.notices and dep.notices[0] == "Cancelled"
    assert "Partial cancellation" in dep.notices


def test_parse_departure_subway_without_realtime():
    p = _provider()
    events = p._parse_board(SAMPLE_BOARD.encode("utf-8"))
    dep = p.parse_departure(events[2], TZ, NOW)
    assert dep is not None
    assert dep.transportation_type == "subway"
    assert dep.is_realtime is False
    assert dep.delay == 0
    assert dep.departure_time == dep.planned_time == "18:50"
    assert dep.platform == "1"


def test_parse_departure_tram_decodes_entities():
    p = _provider()
    events = p._parse_board(SAMPLE_BOARD.encode("utf-8"))
    dep = p.parse_departure(events[3], TZ, NOW)
    assert dep is not None
    assert dep.transportation_type == "tram"
    assert dep.destination == "Raxstraße"     # &#223; decoded to ß


# -- transport type mapping -------------------------------------------------

def test_transport_type_category_precedence_over_class():
    p = _provider()
    # Unknown category falls back to the class bitmask ...
    assert p._transport_type({"prod": "X#WHAT", "class": "64"}) == "bus"
    # ... but a known category wins even if class is absent.
    assert p._transport_type({"prod": "S 2#S", "class": ""}) == "train"
    assert p._transport_type({"prod": "", "class": ""}) == "unknown"


def test_class_bitmask_fallback():
    assert hb.DEFAULT_CLASS_MAPPING[256] == "subway"
    assert hb.DEFAULT_CLASS_MAPPING[512] == "tram"
    assert hb.DEFAULT_CLASS_MAPPING[64] == "bus"


# -- ÖBB subclass wiring ----------------------------------------------------

def test_oebb_configuration():
    p = _provider()
    assert p.provider_id == "oebb"
    assert p.get_timezone() == "Europe/Vienna"
    assert p.hafas_base_url == "https://fahrplan.oebb.at/bin"
    assert isinstance(p, HafasBaseProvider)
    # ÖBB-specific category extension present.
    assert p.get_category_mapping()["CJX"] == "train"
