"""GVH (Großraum-Verkehr Hannover) provider implementation."""

from typing import Any, Dict

from ..const import PROVIDER_GVH
from .efa_base import EFABaseProvider

# Standard EFA product classes.
_TRANSPORT_TYPES: Dict[Any, str] = {
    0: "train",  # High-speed trains (ICE, IC, EC)
    1: "train",  # S-Bahn
    2: "subway",  # U-Bahn
    3: "tram",  # Stadtbahn
    4: "tram",  # Tram/Streetcar
    5: "bus",  # City bus
    6: "bus",  # Regional bus
    7: "bus",  # Express bus
    8: "tram",  # Cable / rack railway
    9: "ferry",  # Ferry/Ship
    10: "bus",  # Demand-responsive transport (AST/Rufbus)
    11: "bus",  # Other
    13: "train",  # Regional train (RE/RB)
    14: "train",  # National train
    15: "train",  # International train
    16: "train",  # High-speed train
    17: "bus",  # Rail replacement service (SEV)
    18: "train",  # Shuttle train
    19: "bus",  # Bürgerbus
}


class GVHProvider(EFABaseProvider):
    """GVH (Hannover) provider."""

    @property
    def provider_id(self) -> str:
        return PROVIDER_GVH

    @property
    def provider_name(self) -> str:
        return "GVH (Hannover)"

    @property
    def dm_base_url(self) -> str:
        return "https://www.efa.de/efa/XML_DM_REQUEST"

    @property
    def sf_base_url(self) -> str:
        return "https://www.efa.de/efa/XML_STOPFINDER_REQUEST"

    def get_timezone(self) -> str:
        return "Europe/Berlin"

    def get_transport_type_mapping(self) -> Dict[Any, str]:
        return _TRANSPORT_TYPES
