"""Connect authentication, protocol errors and realtime parsing."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock
from xml.etree import ElementTree as ET
from zoneinfo import ZoneInfo

import pytest

from openpublictransport import get_provider
from openpublictransport.exceptions import ApiResponseError, AuthenticationError, ApiTimeoutError
from openpublictransport.providers.connect import ConnectTriasProvider
from openpublictransport.providers.trias_base import NS

URL = "https://v4-api.efa.de"


def session_response(body, status=200, timeout=False):
    response = MagicMock(status=status)
    response.text = AsyncMock(return_value=body)
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=response)
    if timeout:
        context.__aenter__.side_effect = TimeoutError()
    context.__aexit__ = AsyncMock(return_value=False)
    session = MagicMock()
    session.post.return_value = context
    return session


def envelope(payload):
    return f'<Trias xmlns="{NS["trias"]}" xmlns:siri="{NS["siri"]}" version="1.2"><ServiceDelivery><DeliveryPayload>{payload}</DeliveryPayload></ServiceDelivery></Trias>'


def location(i="de:03241:11", name="Christuskirche"):
    return f"<LocationResult><Location><StopPoint><StopPointRef>{i}</StopPointRef><StopPointName><Text>{name}</Text></StopPointName></StopPoint><LocationName><Text>Hannover</Text></LocationName></Location></LocationResult>"


def event(estimated="2026-10-14T08:03:00Z"):
    prediction = f"<EstimatedTime>{estimated}</EstimatedTime>" if estimated else ""
    return f"<StopEventResult><StopEvent><ThisCall><CallAtStop><ServiceDeparture><TimetabledTime>2026-10-14T08:00:00Z</TimetabledTime>{prediction}</ServiceDeparture><PlannedBay><Text>1</Text></PlannedBay><EstimatedBay><Text>2</Text></EstimatedBay></CallAtStop></ThisCall><Service><PublishedLineName><Text>6</Text></PublishedLineName><DestinationText><Text>Nordhafen</Text></DestinationText><Mode><PtMode>tram</PtMode></Mode></Service></StopEvent></StopEventResult>"


def test_registered():
    p = get_provider("connect_trias", None, api_key="test")
    assert isinstance(p, ConnectTriasProvider)
    assert p.requires_api_key
    assert p.trias_version == "1.2"
    assert p._extra_headers() == {}


@pytest.mark.parametrize(
    "planned,estimated,expected",
    [
        ("2026-10-15T21:07:40Z", "2026-10-15T21:08:15Z", 1),
        ("2026-10-15T21:03:40Z", "2026-10-15T21:05:15Z", 2),
        ("2026-10-15T23:59:40Z", "2026-10-16T00:00:15Z", 1),
        ("2026-10-15T21:08:40Z", "2026-10-15T21:07:15Z", 0),
    ],
)
def test_delay_matches_displayed_minutes_across_seconds_and_midnight(planned, estimated, expected):
    provider = ConnectTriasProvider(None, api_key="dummy-test-key")
    departure = provider.parse_departure(
        {"timetabledTime": planned, "estimatedTime": estimated},
        ZoneInfo("Europe/Berlin"),
        datetime(2026, 10, 15, 20, tzinfo=timezone.utc),
    )
    assert departure.is_realtime
    assert departure.delay == expected


@pytest.mark.parametrize("kind", ["search", "departures"])
async def test_xml_escapes_all_inputs_and_authenticates_only_in_body(kind):
    key = "dummy<&>key"
    session = session_response(
        envelope("<LocationInformationResponse/>" if kind == "search" else "<StopEventResponse/>")
    )
    p = ConnectTriasProvider(session, api_key=key)
    if kind == "search":
        await p.search_stops("Hannover & <Test>")
    else:
        await p.fetch_departures("de:03241:11&x", "", "", 10)
    call = session.post.call_args
    assert call.args == (URL,)
    root = ET.fromstring(call.kwargs["data"])
    assert root.find("trias:ServiceRequest/siri:RequestorRef", NS).text == key
    assert "Authorization" not in call.kwargs["headers"]
    assert call.kwargs["allow_redirects"] is False
    assert root.attrib["version"] == "1.2"
    if kind == "departures":
        timestamp = root.find(".//trias:DepArrTime", NS).text
        assert datetime.fromisoformat(timestamp).tzinfo is not None
        assert root.find(".//trias:StopPointRef", NS).text == "de:03241:11&x"
    else:
        assert root.find(".//trias:LocationName", NS).text == "Hannover & <Test>"


@pytest.mark.parametrize("count", [0, 1, 2])
async def test_location_results(count):
    p = ConnectTriasProvider(None, api_key="test")
    p._request = AsyncMock(
        return_value=envelope(
            "<LocationInformationResponse>"
            + "".join(location(str(i)) for i in range(count))
            + "</LocationInformationResponse>"
        )
    )
    stops = await p.search_stops("Hannover")
    assert len(stops) == count
    if count:
        assert stops[0] == {"id": "0", "name": "Christuskirche", "place": "Hannover", "area_type": "stop"}


@pytest.mark.parametrize("count", [0, 1, 2])
@pytest.mark.parametrize("estimated", ["2026-10-14T08:03:00Z", "", "not-a-date", "2026-10-14T08:03:00"])
async def test_departures_and_optional_realtime(count, estimated):
    p = ConnectTriasProvider(None, api_key="test")
    p._request = AsyncMock(
        return_value=envelope("<StopEventResponse>" + event(estimated) * count + "</StopEventResponse>")
    )
    data = await p.fetch_departures("de:03241:11", "", "", 10)
    assert len(data["stopEvents"]) == count
    if count:
        d = p.parse_departure(
            data["stopEvents"][0], ZoneInfo("Europe/Berlin"), datetime(2026, 10, 14, 7, 55, tzinfo=timezone.utc)
        )
        assert d.line == "6" and d.destination == "Nordhafen"
        assert d.is_realtime == (estimated == "2026-10-14T08:03:00Z")
        assert d.delay == (3 if d.is_realtime else 0)
        assert d.platform == "2" and d.platform_changed


@pytest.mark.parametrize(
    "body",
    [
        None,
        "",
        "<bad",
        "<html/>",
        envelope("<TripResponse/>"),
        envelope("<StopEventResponse><ErrorMessage><Code>BAD_REQUEST</Code></ErrorMessage></StopEventResponse>"),
        '<Trias xmlns="http://www.vdv.de/trias"><ServiceDelivery><siri:Status xmlns:siri="http://www.siri.org.uk/siri">false</siri:Status></ServiceDelivery></Trias>',
    ],
)
async def test_bad_responses_never_become_empty_departures(body):
    p = ConnectTriasProvider(None, api_key="test")
    p._request = AsyncMock(return_value=body)
    with pytest.raises(ApiResponseError):
        await p.fetch_departures("de:03241:11", "", "", 10)


async def test_missing_key_makes_no_request():
    p = ConnectTriasProvider(None)
    p._request = AsyncMock()
    with pytest.raises(AuthenticationError):
        await p.search_stops("Hannover")
    p._request.assert_not_called()


@pytest.mark.parametrize("status", [401, 403, 429, 500])
async def test_http_error_does_not_echo_key(status):
    session = session_response("private-key", status=status)
    p = ConnectTriasProvider(session, api_key="private-key")
    with pytest.raises(Exception) as exc:
        await p.search_stops("Hannover")
    assert "private-key" not in str(exc.value)
    assert exc.value.body is None
    session.post.assert_called_once()


async def test_timeout_is_propagated():
    p = ConnectTriasProvider(session_response("", timeout=True), api_key="test")
    with pytest.raises(ApiTimeoutError):
        await p.search_stops("Hannover")


@pytest.mark.parametrize("line,mode,transport", [("100", "bus", "bus"), ("5", "urbanRail", "train")])
async def test_connect_service_section_from_live_responses(line, mode, transport):
    """Shape observed on Christuskirche and Leibniz Universität on 2026-10-14."""
    root = ET.fromstring(envelope("<StopEventResponse>" + event() + "</StopEventResponse>"))
    service = root.find(".//trias:Service", NS)
    section = ET.Element(f"{{{NS['trias']}}}ServiceSection")
    for field in ("PublishedLineName", "Mode"):
        child = service.find(f"trias:{field}", NS)
        service.remove(child)
        section.append(child)
    section.find("trias:PublishedLineName/trias:Text", NS).text = line
    section.find("trias:Mode/trias:PtMode", NS).text = mode
    ET.SubElement(section, f"{{{NS['trias']}}}OperatorRef").text = "gvh:02"
    service.insert(0, section)
    provider = ConnectTriasProvider(None, api_key="dummy-test-key")
    provider._request = AsyncMock(return_value=ET.tostring(root, encoding="unicode"))
    data = await provider.fetch_departures("de:03241:171", "", "", 10)
    departure = provider.parse_departure(
        data["stopEvents"][0], ZoneInfo("Europe/Berlin"), datetime(2026, 10, 14, 7, 55, tzinfo=timezone.utc)
    )
    assert departure.line == line
    assert departure.transportation_type == transport
    assert departure.agency == "gvh:02"
    assert departure.destination == "Nordhafen"
