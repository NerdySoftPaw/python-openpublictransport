"""INSA / NASA — Personennahverkehr in Sachsen-Anhalt provider.

Modern HAFAS ``mgate.exe`` gateway; unsigned. Config from
public-transport/transport-apis (``de/nasa-hafas-mgate``). The access id is the one used
by the operator's own app, not an officially issued key, so it may be rotated
without notice.
"""

from ..const import PROVIDER_INSA
from .hafas_mgate_base import HafasMgateBaseProvider


class INSAProvider(HafasMgateBaseProvider):
    """INSA (Sachsen-Anhalt) via HAFAS mgate."""

    mgate_endpoint = "https://reiseauskunft.insa.de/bin/mgate.exe"
    mgate_auth = {"type": "AID", "aid": "nasa-apps"}
    mgate_client = {"type": "IPH", "id": "NASA", "v": "4000200", "name": "nasaPROD", "os": "iPhone OS 11.2.5"}
    mgate_ver = "1.18"
    mgate_lang = "de"
    timezone = "Europe/Berlin"

    @property
    def provider_id(self) -> str:
        return PROVIDER_INSA

    @property
    def provider_name(self) -> str:
        return "INSA (Sachsen-Anhalt)"
