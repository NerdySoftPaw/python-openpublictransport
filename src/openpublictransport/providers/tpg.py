"""TPG — Transports publics genevois (Geneva, Switzerland) provider.

Modern HAFAS ``mgate.exe`` gateway; unsigned. Config from
public-transport/transport-apis (``ch/tpg-hafas-mgate``).
"""

from ..const import PROVIDER_TPG_CH
from .hafas_mgate_base import HafasMgateBaseProvider


class TPGProvider(HafasMgateBaseProvider):
    """Transports publics genevois (Geneva) via HAFAS mgate."""

    mgate_endpoint = "https://tpg.hafas.cloud/bin/mgate.exe"
    mgate_auth = {"type": "AID", "aid": "9CZsdl5PqX8n5D6b"}
    mgate_client = {"id": "HAFAS", "type": "WEB", "name": "webapp", "l": "vs_webapp"}
    mgate_ver = "1.40"
    timezone = "Europe/Zurich"

    @property
    def provider_id(self) -> str:
        return PROVIDER_TPG_CH

    @property
    def provider_name(self) -> str:
        return "TPG (Genève)"
