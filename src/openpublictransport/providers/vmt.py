"""VMT — Verkehrsverbund Mittelthüringen provider.

Modern HAFAS ``mgate.exe`` gateway; unsigned. Config from
public-transport/transport-apis (``de/vmt-hafas-mgate``). The access id is the one used
by the operator's own app, not an officially issued key, so it may be rotated
without notice.
"""

from ..const import PROVIDER_VMT
from .hafas_mgate_base import HafasMgateBaseProvider


class VMTProvider(HafasMgateBaseProvider):
    """VMT (Thüringen) via HAFAS mgate."""

    mgate_endpoint = "https://vmt.hafas.de/bin/ticketing/mgate.exe"
    mgate_auth = {"type": "AID", "aid": "t2h7u1e6r4i8n3g7e0n"}
    mgate_client = {"type": "IPH", "id": "HAFAS", "name": "VMT", "v": "2040100"}
    mgate_ver = "1.18"
    mgate_lang = "de"
    timezone = "Europe/Berlin"

    @property
    def provider_id(self) -> str:
        return PROVIDER_VMT

    @property
    def provider_name(self) -> str:
        return "VMT (Thüringen)"
