"""VOS — Verkehrsgemeinschaft Osnabrück provider.

Modern HAFAS ``mgate.exe`` gateway; unsigned. Config from
public-transport/transport-apis (``de/vos-hafas-mgate``). The access id is the one used by
the operator's own app, not an officially issued key, so it may be rotated
without notice.
"""

from ..const import PROVIDER_VOS
from .hafas_mgate_base import HafasMgateBaseProvider


class VOSProvider(HafasMgateBaseProvider):
    """VOS (Osnabrück) via HAFAS mgate."""

    mgate_endpoint = "https://fahrplan.vos.info/bin/mgate.exe"
    mgate_auth = {"type": "AID", "aid": "PnYowCQP7Tp1V"}
    mgate_client = {"id": "SWO", "type": "WEB", "name": "webapp", "l": "vs_swo"}
    mgate_ver = "1.34"
    mgate_lang = "de"
    timezone = "Europe/Berlin"

    @property
    def provider_id(self) -> str:
        return PROVIDER_VOS

    @property
    def provider_name(self) -> str:
        return "VOS (Osnabrück)"
