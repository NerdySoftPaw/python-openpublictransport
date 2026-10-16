"""NVV — Nordhessischer Verkehrsverbund provider.

Modern HAFAS ``mgate.exe`` gateway; unsigned. Config from
public-transport/transport-apis (``de/nvv-hafas-mgate``). The access id is the one used by
the operator's own app, not an officially issued key, so it may be rotated
without notice.
"""

from ..const import PROVIDER_NVV
from .hafas_mgate_base import HafasMgateBaseProvider


class NVVProvider(HafasMgateBaseProvider):
    """NVV (Nordhessen) via HAFAS mgate."""

    mgate_endpoint = "https://auskunft.nvv.de/auskunft/bin/app/mgate.exe"
    mgate_auth = {"type": "AID", "aid": "Kt8eNOH7qjVeSxNA"}
    mgate_client = {"type": "IPH", "id": "NVV", "v": "5000300", "os": "iOS 12.1.4", "name": "NVVMobilPROD_APPSTORE"}
    mgate_ver = "1.18"
    mgate_lang = "de"
    mgate_ext = "NVV.6.0"
    timezone = "Europe/Berlin"

    @property
    def provider_id(self) -> str:
        return PROVIDER_NVV

    @property
    def provider_name(self) -> str:
        return "NVV (Nordhessen)"
