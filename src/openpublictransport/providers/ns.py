"""NS / Nederlandse Spoorwegen (Netherlands) provider.

Uses the legacy HAFAS "Scotty" endpoints at ``hafas.bene-system.com`` (the
deployment catalogued by public-transport/transport-apis as the NS
``hafasQuery`` interface). Stop finder and departure board both respond over
plain HTTP GET; no API key required.
"""

from ..const import PROVIDER_NS_NL
from .hafas_base import HafasBaseProvider


class NSProvider(HafasBaseProvider):
    """NS (Netherlands) via HAFAS Scotty endpoints."""

    hafas_base_url = "https://hafas.bene-system.com/bin"
    timezone = "Europe/Amsterdam"

    @property
    def provider_id(self) -> str:
        return PROVIDER_NS_NL

    @property
    def provider_name(self) -> str:
        return "NS (Nederland)"
