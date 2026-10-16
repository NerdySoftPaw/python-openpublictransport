"""TriMet (Portland, Oregon) provider via its OpenTripPlanner REST API (OTP 1 ``/index``)."""

from ..const import PROVIDER_TRIMET_US
from .otp_base import OTPBaseProvider


class TriMetProvider(OTPBaseProvider):
    """TriMet (Portland) — no API key."""

    otp_base_url = "https://maps.trimet.org/otp_mod"
    geocode_countries = "us"

    @property
    def provider_id(self) -> str:
        return PROVIDER_TRIMET_US

    @property
    def provider_name(self) -> str:
        return "TriMet (Portland)"

    def get_timezone(self) -> str:
        return "America/Los_Angeles"
