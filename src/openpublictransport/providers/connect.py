"""Connect OpenService TRIAS 1.2 (Connect Fahrplanauskunft GmbH)."""

from datetime import datetime
from typing import Any, Optional
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

from ..const import PROVIDER_CONNECT_TRIAS
from ..exceptions import ApiResponseError, AuthenticationError
from .trias_base import NS, TRIASBaseProvider, _find, _parse_dt, _text


class ConnectTriasProvider(TRIASBaseProvider):
    """Authenticate using RequestorRef in the POST body, never HTTP headers."""

    trias_base_url = "https://v4-api.efa.de"
    trias_version = "1.2"

    @property
    def provider_id(self) -> str:
        return PROVIDER_CONNECT_TRIAS

    @property
    def provider_name(self) -> str:
        return "Connect Fahrplanauskunft"

    @property
    def requires_api_key(self) -> bool:
        return True

    @property
    def requestor_ref(self) -> str:
        return self.api_key or ""

    def _build_location_request(self, search_term: str) -> str:
        return super()._build_location_request(escape(search_term))

    def _build_stop_event_request(self, stop_id: str, limit: int) -> str:
        if not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValueError("NumberOfResults must be between 1 and 100")
        return super()._build_stop_event_request(escape(stop_id), limit)

    async def _error_body(self, response: Any, limit: int = 300) -> Optional[str]:
        # Servers may echo submitted credentials. Do not include response bodies
        # in logs, repair issues, or exceptions for this authenticated endpoint.
        return None

    async def _request(self, method: str, url: str, **kwargs: Any) -> Any:
        # Do not forward the authenticated XML body to a redirected endpoint.
        kwargs["allow_redirects"] = False
        return await super()._request(method, url, **kwargs)

    async def _post_trias(self, xml_body: str) -> ET.Element:
        if not self.api_key or not self.api_key.strip():
            raise AuthenticationError(self.provider_name)
        # The base class interpolates RequestorRef; escape that value without
        # changing the credential held by the provider.
        xml_body = xml_body.replace(
            f"<siri:RequestorRef>{self.requestor_ref}</siri:RequestorRef>",
            f"<siri:RequestorRef>{escape(self.requestor_ref)}</siri:RequestorRef>",
            1,
        )
        request = ET.fromstring(xml_body)
        payload = request.find("trias:ServiceRequest/trias:RequestPayload", NS)
        response_types = {
            "LocationInformationRequest": "LocationInformationResponse",
            "StopEventRequest": "StopEventResponse",
            "TripRequest": "TripResponse",
        }
        if payload is None or len(payload) != 1:
            raise ValueError("Expected one TRIAS request payload")
        expected = response_types.get(payload[0].tag.rsplit("}", 1)[-1])
        if expected is None:
            raise ValueError("Unsupported TRIAS request payload")
        text = await self._request(
            "post",
            self.trias_base_url,
            data=xml_body.encode("utf-8"),
            headers={"Content-Type": "text/xml; charset=utf-8"},
            timeout=15,
            response_format="text",
        )
        if not isinstance(text, str) or not text.strip():
            raise ApiResponseError(f"{self.provider_name}: empty TRIAS response")
        try:
            root = ET.fromstring(text)
        except ET.ParseError:
            raise ApiResponseError(f"{self.provider_name}: malformed TRIAS XML") from None
        if root.tag != f"{{{NS['trias']}}}Trias":
            raise ApiResponseError(f"{self.provider_name}: unexpected TRIAS envelope")
        delivery = root.find("trias:ServiceDelivery", NS)
        if delivery is None:
            raise ApiResponseError(f"{self.provider_name}: missing ServiceDelivery")
        for element in delivery.iter():
            local_name = element.tag.rsplit("}", 1)[-1]
            if (
                local_name == "ErrorCondition"
                or (local_name == "ErrorMessage" and expected != "TripResponse")
                or (local_name == "Status" and (element.text or "").strip().lower() == "false")
            ):
                raise ApiResponseError(f"{self.provider_name}: TRIAS reported an error")
        if delivery.findall("trias:ErrorMessage", NS):
            raise ApiResponseError(f"{self.provider_name}: TRIAS delivery rejected")
        if delivery.find(f"trias:DeliveryPayload/trias:{expected}", NS) is None:
            raise ApiResponseError(f"{self.provider_name}: missing {expected}")
        return root

    def _stop_event_to_dict(self, stop_event: ET.Element) -> dict:
        data = super()._stop_event_to_dict(stop_event)
        section = _find(stop_event, "trias:Service/trias:ServiceSection")
        if section is not None:
            # Connect nests line, mode and operator in ServiceSection (TRIAS
            # 1.2), while the base provider expects them directly under Service.
            data["lineName"] = _text(section, "trias:PublishedLineName/trias:Text")
            data["mode"] = _text(section, "trias:Mode/trias:PtMode")
            data["submode"] = (
                _text(section, "trias:Mode/trias:RailSubmode")
                or _text(section, "trias:Mode/trias:BusSubmode")
                or _text(section, "trias:Mode/trias:TramSubmode")
                or _text(section, "trias:Mode/trias:MetroSubmode")
            )
            data["operator"] = _text(section, "trias:OperatorRef")
        return data

    def parse_departure(self, stop: dict, tz: Any, now: datetime):
        # A malformed prediction must not be presented as a realtime value.
        stop = dict(stop)
        estimated = _parse_dt(stop.get("estimatedTime", ""))
        stop["isRealtime"] = estimated is not None and estimated.tzinfo is not None
        if not stop["isRealtime"]:
            stop["estimatedTime"] = ""
        planned = _parse_dt(stop.get("timetabledTime", ""))
        if planned is None or planned.tzinfo is None:
            return None
        departure = super().parse_departure(stop, tz, now)
        if departure is not None and stop["isRealtime"]:
            # Match the displayed minute precision and the provider's board.
            # 23:07:40 -> 23:08:15 must not display a zero-minute delay.
            departure.delay = max(
                0,
                int(
                    (
                        estimated.replace(second=0, microsecond=0) - planned.replace(second=0, microsecond=0)
                    ).total_seconds()
                    / 60
                ),
            )
        return departure
