"""VAO — Verkehrsauskunft Österreich provider.

Modern HAFAS ``mgate.exe`` gateway; unsigned. Config from
public-transport/transport-apis (``at/stv-hafas-mgate``). The access id is the one used by
the operator's own app, not an officially issued key, so it may be rotated
without notice.

All Austrian VAO deployments serve the same nationwide dataset; this uses the
Verbundlinie (Steiermark) web client. The catalogue path /bin/mgate.exe now
redirects to /hamm/gate, which is used directly.
"""

from ..const import PROVIDER_VAO_AT
from .hafas_mgate_base import HafasMgateBaseProvider


class VAOProvider(HafasMgateBaseProvider):
    """VAO (Austria-wide) via the Verbundlinie HAFAS mgate."""

    mgate_endpoint = "https://verkehrsauskunft.verbundlinie.at/hamm/gate"
    mgate_auth = {"type": "AID", "aid": "wf7mcf9bv3nv8g5f"}
    mgate_client = {"id": "VAO", "type": "WEB", "name": "webapp", "l": "vs_stv"}
    mgate_ver = "1.32"
    mgate_lang = "de"
    mgate_ext = "VAO.13"
    timezone = "Europe/Vienna"

    @property
    def provider_id(self) -> str:
        return PROVIDER_VAO_AT

    @property
    def provider_name(self) -> str:
        return "VAO (Österreich)"
