"""TRIAS routing protocol, qualified results and travel-time boundaries."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock
from xml.etree import ElementTree as ET

import pytest

from openpublictransport.exceptions import ApiResponseError
from openpublictransport.providers.connect import ConnectTriasProvider
from openpublictransport.providers.trias_base import NS


def envelope(payload):
    return f'<Trias xmlns="{NS["trias"]}" xmlns:siri="{NS["siri"]}"><ServiceDelivery><DeliveryPayload>{payload}</DeliveryPayload></ServiceDelivery></Trias>'


def timed_leg(
    line="6", dep="2026-10-15T23:58:00Z", arr="2026-10-16T00:03:00Z", estimated="2026-10-16T00:00:00Z", cancelled=False
):
    return f"""<TripLeg><TimedLeg>
      <LegBoard><StopPointRef>A</StopPointRef><StopPointName><Text>Christuskirche</Text></StopPointName>
      <PlannedBay><Text>1</Text></PlannedBay><EstimatedBay><Text>2</Text></EstimatedBay>
      <ServiceDeparture><TimetabledTime>{dep}</TimetabledTime><EstimatedTime>{estimated}</EstimatedTime></ServiceDeparture></LegBoard>
      <LegAlight><StopPointName><Text>Leibniz Universität</Text></StopPointName>
      <ServiceArrival><TimetabledTime>{arr}</TimetabledTime></ServiceArrival></LegAlight>
      <Service><Cancelled>{str(cancelled).lower()}</Cancelled><ServiceSection><PublishedLineName><Text>{line}</Text></PublishedLineName>
      <Mode><PtMode>tram</PtMode></Mode><OperatorRef>operator</OperatorRef></ServiceSection></Service>
    </TimedLeg></TripLeg>"""


def walk_leg(kind="InterchangeLeg", duration="PT2M"):
    return f"<TripLeg><{kind}><LegStart><StopPointRef>A</StopPointRef></LegStart><LegEnd><StopPointRef>B</StopPointRef></LegEnd><Duration>{duration}</Duration></{kind}></TripLeg>"


def response(legs=None, warning=""):
    message = f"<ErrorMessage><Code>{warning}</Code></ErrorMessage>" if warning else ""
    return envelope(f"""<TripResponse><TripResult>{message}<Trip><TripId>test</TripId>
      <Duration>PT5M</Duration><StartTime>2026-10-15T23:58:00Z</StartTime><EndTime>2026-10-16T00:03:00Z</EndTime>
      <Interchanges>0</Interchanges>{legs or timed_leg()}</Trip></TripResult></TripResponse>""")


async def fetch(body):
    provider = ConnectTriasProvider(None, api_key="dummy-api-key")
    provider._request = AsyncMock(return_value=body)
    return await provider.fetch_trips("A", "B")


async def test_trip_xml_uses_same_authentication_and_escapes_stop_ids():
    key = "dummy<&>key"
    provider = ConnectTriasProvider(None, api_key=key)
    provider._request = AsyncMock(return_value=envelope("<TripResponse/>"))
    departure = datetime(2026, 10, 15, 12, tzinfo=timezone.utc)
    assert await provider.fetch_trips("A<&", "B&>", departure, limit=3) == []
    call = provider._request.call_args
    root = ET.fromstring(call.kwargs["data"])
    assert root.find("trias:ServiceRequest/siri:RequestorRef", NS).text == key
    assert root.find(".//trias:Origin/trias:LocationRef/trias:StopPointRef", NS).text == "A<&"
    assert root.find(".//trias:Destination/trias:LocationRef/trias:StopPointRef", NS).text == "B&>"
    assert root.find(".//trias:Origin/trias:DepArrTime", NS).text == departure.isoformat()
    assert root.find(".//trias:IncludeEstimatedTimes", NS).text == "true"
    assert call.args == ("post", "https://v4-api.efa.de")
    assert "Authorization" not in call.kwargs["headers"]


async def test_direct_trip_has_full_times_realtime_platforms_and_service_section():
    (trip,) = await fetch(response())
    (leg,) = trip["legs"]
    assert trip["duration"] == 300 and trip["transfers"] == 0
    assert leg["departure_planned"] == "2026-10-15T23:58:00+00:00"
    assert leg["arrival_planned"] == "2026-10-16T00:03:00+00:00"
    assert leg["departure_estimated"] == "2026-10-16T00:00:00+00:00"
    assert leg["line"] == "6" and leg["transport_type"] == "tram"
    assert leg["platform"] == "2" and leg["planned_platform"] == "1"
    assert not trip["cancelled"]


@pytest.mark.parametrize("prediction", ["", "invalid", "2026-10-16T00:00:00"])
async def test_invalid_or_missing_realtime_keeps_planned_trip(prediction):
    (trip,) = await fetch(response(timed_leg(estimated=prediction)))
    assert trip["legs"][0]["departure_estimated"] is None


@pytest.mark.parametrize("kind", ["ContinuousLeg", "InterchangeLeg"])
async def test_walk_in_and_walk_out_keep_travel_order_and_derive_times(kind):
    (trip,) = await fetch(response(walk_leg(kind) + timed_leg(estimated="") + walk_leg(kind)))
    assert len(trip["legs"]) == 3
    assert trip["legs"][0]["transport_type"] == "walk"
    assert trip["legs"][0]["departure_planned"] == "2026-10-15T23:56:00+00:00"
    assert trip["legs"][-1]["arrival_planned"] == "2026-10-16T00:05:00+00:00"


async def test_two_vehicle_legs_with_an_interchange():
    legs = timed_leg(estimated="") + walk_leg() + timed_leg("11", "2026-10-16T00:06:00Z", "2026-10-16T00:10:00Z", "")
    (trip,) = await fetch(response(legs).replace("<Interchanges>0", "<Interchanges>1"))
    assert [leg["line"] for leg in trip["legs"]] == ["6", "", "11"]
    assert trip["transfers"] == 1


@pytest.mark.parametrize("code", ["TRIP_REALTIMEINCOMPLETE", "TRIP_ORIGINEQUIVALENT"])
async def test_qualified_trip_is_not_a_fatal_api_error(code):
    (trip,) = await fetch(response(warning=code))
    assert trip["warnings"] == [code]


async def test_no_route_is_successful_empty_result():
    assert (
        await fetch(
            envelope("<TripResponse><ErrorMessage><Code>TRIP_NOTRIPFOUND</Code></ErrorMessage></TripResponse>")
        )
        == []
    )


@pytest.mark.parametrize(
    "body",
    [
        envelope("<StopEventResponse/>"),
        envelope(
            "<TripResponse><ErrorMessage><Code>TRIP_ORIGINUNKNOWN</Code><Text>private-key</Text></ErrorMessage></TripResponse>"
        ),
        response(warning="UNKNOWN"),
        response().replace("PT5M", "bad-duration"),
        response().replace("2026-10-15T23:58:00Z", "invalid"),
        response().replace("<TimedLeg>", "<UnsupportedLeg>").replace("</TimedLeg>", "</UnsupportedLeg>"),
        response().replace("<Interchanges>0", "<Interchanges>-1"),
    ],
)
async def test_bad_trip_responses_fail_without_echoing_server_text(body):
    with pytest.raises(ApiResponseError) as error:
        await fetch(body)
    assert "private-key" not in str(error.value)


async def test_cancelled_trip_is_marked():
    (trip,) = await fetch(response(timed_leg(cancelled=True)))
    assert trip["cancelled"]


@pytest.mark.parametrize("limit", [0, 11, True])
async def test_invalid_limits_do_not_make_a_request(limit):
    provider = ConnectTriasProvider(None, api_key="dummy-api-key")
    provider._request = AsyncMock()
    with pytest.raises(ValueError):
        await provider.fetch_trips("A", "B", limit=limit)
    provider._request.assert_not_awaited()


async def test_naive_request_time_is_rejected():
    with pytest.raises(ValueError):
        await ConnectTriasProvider(None, api_key="dummy-api-key").fetch_trips("A", "B", datetime(2026, 10, 15))
