"""NAH.SH — Nahverkehrsverbund Schleswig-Holstein provider.

Modern HAFAS ``mgate.exe`` gateway; unsigned. Config from
public-transport/transport-apis (``de/nahsh-hafas-mgate``). The access id is the one used
by the operator's own app, not an officially issued key, so it may be rotated
without notice.
"""

from ..const import PROVIDER_NAHSH
from .hafas_mgate_base import HafasMgateBaseProvider


class NAHSHProvider(HafasMgateBaseProvider):
    """NAH.SH (Schleswig-Holstein) via HAFAS mgate."""

    mgate_endpoint = "https://nahsh.hafas.cloud/gate"
    mgate_auth = {"type": "AID", "aid": "r0Ot9FLFNAFxijLW"}
    mgate_client = {"id": "NAHSH", "name": "NAHSHPROD", "type": "IPH", "v": "3000700"}
    mgate_ver = "1.16"
    mgate_lang = "de"
    timezone = "Europe/Berlin"

    @property
    def provider_id(self) -> str:
        return PROVIDER_NAHSH

    @property
    def provider_name(self) -> str:
        return "NAH.SH (Schleswig-Holstein)"
