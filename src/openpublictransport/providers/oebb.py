"""ÖBB (Österreichische Bundesbahnen) provider implementation.

Uses ÖBB's own "Scotty" HAFAS endpoints at ``fahrplan.oebb.at``. The previous
FPTF REST backend (``oebb.macistry.com``) was permanently suspended by its
operator, so this talks to ÖBB's official infrastructure instead. See
https://github.com/NerdySoftPaw/openpublictransport/issues/50.
"""

from typing import Dict

from ..const import PROVIDER_OEBB
from .hafas_base import DEFAULT_CATEGORY_MAPPING, HafasBaseProvider

# ÖBB-specific product categories on top of the shared defaults.
OEBB_CATEGORY_MAPPING: Dict[str, str] = {
    **DEFAULT_CATEGORY_MAPPING,
    "EST": "train",   # Eurostar / Railjet international
    "ARZ": "train",   # Autoreisezug (car-carrying train)
    "ATB": "train",   # Achenseebahn and similar
    "MZB": "train",   # Mariazellerbahn
    "STB": "train",   # Steiermarkbahn
    "GKB": "train",   # Graz-Köflacher Bahn
    "CJX": "train",   # Cityjet Xpress
    "O-BUS": "bus",   # trolleybus (Salzburg/Linz)
}


class OeBBProvider(HafasBaseProvider):
    """ÖBB (Austria) via the official Scotty HAFAS endpoints (fahrplan.oebb.at)."""

    hafas_base_url = "https://fahrplan.oebb.at/bin"
    timezone = "Europe/Vienna"

    @property
    def provider_id(self) -> str:
        return PROVIDER_OEBB

    @property
    def provider_name(self) -> str:
        return "ÖBB (Österreich)"

    def get_category_mapping(self) -> Dict[str, str]:
        return OEBB_CATEGORY_MAPPING
