"""Tests for National Rail (UK) OpenLDBWS response parsing.

Regression test for the departure board coming back empty ("Invalid or empty
API response") for every user: real OpenLDBWS responses use namespace prefixes
containing digits (`lt4:`, `lt5:`, `lt7:` …), which the namespace-stripping
regex did not handle, causing an "unbound prefix" XML parse error.
See https://github.com/NerdySoftPaw/openpublictransport/issues/39
"""

from datetime import datetime
from xml.etree import ElementTree as ET
from zoneinfo import ZoneInfo

import openpublictransport.providers.national_rail as nr

# A realistic OpenLDBWS GetDepartureBoardResponse: digit-bearing prefixes
# (lt4/lt5/lt7) and their xmlns declarations, exactly as the live service emits.
SAMPLE = """<?xml version="1.0" encoding="utf-8"?>
<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">
 <soap:Body>
  <GetDepartureBoardResponse xmlns="http://thalesgroup.com/RTTI/2017-10-01/ldb/">
   <GetStationBoardResult xmlns:lt="http://thalesgroup.com/RTTI/2012-01-13/ldb/types" xmlns:lt4="http://thalesgroup.com/RTTI/2015-11-27/ldb/types" xmlns:lt5="http://thalesgroup.com/RTTI/2016-02-16/ldb/types" xmlns:lt7="http://thalesgroup.com/RTTI/2017-02-02/ldb/types">
    <lt4:locationName>Winchester</lt4:locationName>
    <lt4:crs>WIN</lt4:crs>
    <lt7:trainServices>
     <lt7:service>
      <lt4:std>18:05</lt4:std>
      <lt4:etd>On time</lt4:etd>
      <lt4:platform>2</lt4:platform>
      <lt4:operator>South Western Railway</lt4:operator>
      <lt4:operatorCode>SW</lt4:operatorCode>
      <lt5:destination>
       <lt4:location>
        <lt4:locationName>London Waterloo</lt4:locationName>
        <lt4:crs>WAT</lt4:crs>
       </lt4:location>
      </lt5:destination>
     </lt7:service>
     <lt7:service>
      <lt4:std>18:20</lt4:std>
      <lt4:etd>18:27</lt4:etd>
      <lt4:operator>Great Western Railway</lt4:operator>
      <lt4:operatorCode>GW</lt4:operatorCode>
      <lt5:destination>
       <lt4:location>
        <lt4:locationName>Southampton Central</lt4:locationName>
        <lt4:crs>SOU</lt4:crs>
       </lt4:location>
      </lt5:destination>
     </lt7:service>
    </lt7:trainServices>
   </GetStationBoardResult>
  </GetDepartureBoardResponse>
 </soap:Body>
</soap:Envelope>"""


def test_ldbws_request_uses_consistent_version_triple():
    """SOAPAction must be the 2012-01-13 value; a mismatch yields HTTP 500."""
    assert nr._ENDPOINT.endswith("ldb12.asmx")
    assert nr._SOAP_ACTION == "http://thalesgroup.com/RTTI/2012-01-13/ldb/GetDepartureBoard"
    assert 'xmlns:ldb="http://thalesgroup.com/RTTI/2021-11-01/ldb/"' in nr._SOAP_TEMPLATE


def test_strip_namespaces_handles_digit_prefixes():
    """Digit-bearing prefixes must be stripped so the XML parses."""
    stripped = nr._strip_namespaces(SAMPLE)
    # No prefixed tags and no dangling xmlns declarations should remain.
    assert "lt4:" not in stripped
    assert "lt7:" not in stripped
    assert "xmlns" not in stripped
    # Must now be parseable (previously raised "unbound prefix").
    root = ET.fromstring(stripped)
    assert len(root.findall(".//service")) == 2


def test_parses_services_into_departures():
    """The full parse path yields the expected departures."""
    root = ET.fromstring(nr._strip_namespaces(SAMPLE))
    provider = nr.NationalRailProvider(session=None)
    tz = ZoneInfo("Europe/London")
    now = datetime(2026, 7, 2, 18, 0, tzinfo=tz)

    deps = []
    for svc in root.findall(".//service"):
        d = provider._service_to_dict(svc, now.date(), tz, now)
        assert d is not None
        deps.append(provider.parse_departure(d, tz, now))

    assert deps[0].destination == "London Waterloo"
    assert deps[0].planned_time == "18:05"
    assert deps[0].platform == "2"
    assert deps[0].line == "SW"
    assert deps[0].delay == 0

    # Second service is estimated 7 minutes late (18:05 std -> 18:27 etd on 18:20).
    assert deps[1].destination == "Southampton Central"
    assert deps[1].delay == 7
    assert deps[1].is_realtime is True
