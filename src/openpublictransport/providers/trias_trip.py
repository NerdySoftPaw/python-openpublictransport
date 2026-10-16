"""TRIAS 1.2 trip response parsing, independent of presentation and HA."""

import re
from datetime import datetime, timedelta
from xml.etree import ElementTree as ET

from ..exceptions import ApiResponseError
from .trias_base import NS, _find, _parse_dt, _text

# TripResult messages can qualify a usable trip rather than reject the request.
TRIP_WARNINGS = frozenset(
    {
        "TRIP_ORIGINEQUIVALENT",
        "TRIP_DESTINATIONEQUIVALENT",
        "TRIP_VIAEQUIVALENT",
        "TRIP_REALTIMEINCOMPLETE",
        "TRIP_ITTIMEEXTENDED",
        "TRIP_ITMODECHANGED",
        "TRIP_INCONVENIENTWAITING",
    }
)
_DURATION = re.compile(r"P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?)?")


def _fail(message: str):
    # Never include XML contents or server-provided text: it may echo credentials.
    raise ApiResponseError(f"TRIAS: {message}")


def _duration(value: str) -> float:
    match = _DURATION.fullmatch(value)
    if not match or not any(match.groups()):
        _fail("invalid trip duration")
    days, hours, minutes, seconds = (float(v or 0) for v in match.groups())
    return days * 86400 + hours * 3600 + minutes * 60 + seconds


def _timestamp(value: str, *, required: bool = True) -> str | None:
    parsed = _parse_dt(value)
    if parsed is None or parsed.tzinfo is None or parsed.utcoffset() is None:
        if required:
            _fail("invalid trip timestamp")
        return None
    return parsed.isoformat()


def _codes(element: ET.Element) -> list[str]:
    return [_text(error, "trias:Code") for error in element.findall("trias:ErrorMessage", NS)]


def _location(element: ET.Element | None) -> str:
    return (
        _text(element, "trias:StopPointName/trias:Text")
        or _text(element, "trias:LocationName/trias:Text")
        or _text(element, "trias:StopPointRef")
        or _text(element, "trias:StopPlaceRef")
    )


def _timed_leg(element: ET.Element, modes: dict) -> dict:
    board, alight = _find(element, "trias:LegBoard"), _find(element, "trias:LegAlight")
    service = _find(element, "trias:Service")
    section = _find(service, "trias:ServiceSection")
    if section is None:
        section = service
    dep = _timestamp(_text(board, "trias:ServiceDeparture/trias:TimetabledTime"))
    arr = _timestamp(_text(alight, "trias:ServiceArrival/trias:TimetabledTime"))
    dep_est = _timestamp(_text(board, "trias:ServiceDeparture/trias:EstimatedTime"), required=False)
    arr_est = _timestamp(_text(alight, "trias:ServiceArrival/trias:EstimatedTime"), required=False)
    mode = _text(section, "trias:Mode/trias:PtMode")
    planned_bay = _text(board, "trias:PlannedBay/trias:Text")
    return {
        "origin": _location(board),
        "destination": _location(alight),
        "line": _text(section, "trias:PublishedLineName/trias:Text"),
        "product": mode,
        "transport_type": modes.get(mode, "unknown"),
        "operator": _text(section, "trias:OperatorRef"),
        "departure_planned": dep,
        "departure_estimated": dep_est,
        "arrival_planned": arr,
        "arrival_estimated": arr_est,
        "platform": _text(board, "trias:EstimatedBay/trias:Text") or planned_bay,
        "planned_platform": planned_bay,
        "duration": max(
            0, (datetime.fromisoformat(arr_est or arr) - datetime.fromisoformat(dep_est or dep)).total_seconds()
        ),
        "cancelled": any(
            _text(node, path).lower() == "true"
            for node, path in (
                (service, "trias:Cancelled"),
                (board, "trias:NotServicedStop"),
                (board, "trias:NoBoardingAtStop"),
                (alight, "trias:NotServicedStop"),
                (alight, "trias:NoAlightingAtStop"),
            )
        ),
    }


def _continuous_leg(element: ET.Element, *, interchange: bool) -> dict:
    mode = (
        _text(element, "trias:InterchangeMode")
        if interchange
        else _text(element, "trias:Service/trias:IndividualMode")
    )
    mode = mode or _text(element, "trias:ContinuousMode") or "walk"
    return {
        "origin": _location(_find(element, "trias:LegStart")),
        "destination": _location(_find(element, "trias:LegEnd")),
        "line": "",
        "product": mode,
        "transport_type": "walk" if interchange else mode,
        "duration": _duration(_text(element, "trias:Duration")),
        "departure_planned": _timestamp(_text(element, "trias:TimeWindowStart"), required=False),
        "arrival_planned": _timestamp(_text(element, "trias:TimeWindowEnd"), required=False),
        "departure_estimated": None,
        "arrival_estimated": None,
        "platform": "",
        "cancelled": False,
    }


def parse_trip_response(root: ET.Element, modes: dict) -> list[dict]:
    """Parse TripResponse; preserve qualified results and distinguish no route from failure."""
    response = root.find("trias:ServiceDelivery/trias:DeliveryPayload/trias:TripResponse", NS)
    if response is None:
        _fail("missing TripResponse")
    errors = _codes(response)
    results = response.findall("trias:TripResult", NS)
    if errors:
        if set(errors) == {"TRIP_NOTRIPFOUND"} and not results:
            return []
        _fail("trip request rejected")
    journeys = []
    for result in results:
        warnings = _codes(result)
        if any(code not in TRIP_WARNINGS for code in warnings):
            _fail("trip result rejected")
        trip = _find(result, "trias:Trip")
        if trip is None:
            _fail("missing Trip")
        start = _timestamp(_text(trip, "trias:StartTime"))
        end = _timestamp(_text(trip, "trias:EndTime"))
        legs = []
        for wrapper in trip.findall("trias:TripLeg", NS):
            for kind in ("TimedLeg", "InterchangeLeg", "ContinuousLeg"):
                element = _find(wrapper, "trias:" + kind)
                if element is not None:
                    legs.append(
                        _timed_leg(element, modes)
                        if kind == "TimedLeg"
                        else _continuous_leg(element, interchange=kind == "InterchangeLeg")
                    )
                    break
            else:
                _fail("unknown trip leg")
        if not legs:
            _fail("trip has no legs")
        # Continuous/interchange legs need not carry explicit times. Anchor
        # them to the next timed leg (walk-in) or the previous leg (walk-out).
        for index in range(len(legs) - 1, -1, -1):
            leg = legs[index]
            if not leg["arrival_planned"] and index + 1 < len(legs):
                leg["arrival_planned"] = legs[index + 1]["departure_planned"]
            if not leg["departure_planned"] and leg["arrival_planned"]:
                leg["departure_planned"] = (
                    datetime.fromisoformat(leg["arrival_planned"]) - timedelta(seconds=leg["duration"])
                ).isoformat()
        for index, leg in enumerate(legs):
            if not leg["departure_planned"]:
                leg["departure_planned"] = legs[index - 1]["arrival_planned"] if index else start
            if not leg["arrival_planned"]:
                leg["arrival_planned"] = (
                    datetime.fromisoformat(leg["departure_planned"]) + timedelta(seconds=leg["duration"])
                ).isoformat()
        try:
            transfers = int(_text(trip, "trias:Interchanges"))
            if transfers < 0:
                raise ValueError
        except ValueError:
            _fail("invalid interchange count")
        journeys.append(
            {
                "id": _text(trip, "trias:TripId"),
                "start_time": start,
                "end_time": end,
                "duration": _duration(_text(trip, "trias:Duration")),
                "transfers": transfers,
                "legs": legs,
                "warnings": warnings,
                "cancelled": any(leg["cancelled"] for leg in legs),
            }
        )
    return journeys
