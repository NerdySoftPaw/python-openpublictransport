"""mobilitéit.lu (Luxembourg) provider.

Luxembourg's national multimodal journey planner (train + bus) exposed via the
legacy HAFAS "Scotty" endpoints on the shared HACON host ``cdt.hafas.de``. Stop
finder and departure board respond over plain HTTP GET; no API key required.
"""

from ..const import PROVIDER_MOBILITEIT_LU
from .hafas_base import HafasBaseProvider


class MobiliteitLuProvider(HafasBaseProvider):
    """mobilitéit.lu (Luxembourg) via HAFAS Scotty endpoints."""

    hafas_base_url = "https://cdt.hafas.de/bin"
    timezone = "Europe/Luxembourg"

    @property
    def provider_id(self) -> str:
        return PROVIDER_MOBILITEIT_LU

    @property
    def provider_name(self) -> str:
        return "mobilitéit.lu (Luxembourg)"
