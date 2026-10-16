"""saarVV — Saarländischer Verkehrsverbund provider.

Modern HAFAS ``mgate.exe`` gateway; unsigned. Config from
public-transport/transport-apis (``de/saarvv-hafas-mgate``). The access id is the one used
by the operator's own app, not an officially issued key, so it may be rotated
without notice.
"""

from ..const import PROVIDER_SAARVV
from .hafas_mgate_base import HafasMgateBaseProvider


class SaarVVProvider(HafasMgateBaseProvider):
    """saarVV (Saarland) via HAFAS mgate."""

    mgate_endpoint = "https://saarfahrplan.de/bin/mgate.exe"
    mgate_auth = {"type": "AID", "aid": "51XfsVqgbdA6oXzHrx75jhlocRg6Xe"}
    mgate_client = {"type": "AND", "id": "ZPS-SAAR", "v": 1000070, "name": "Saarfahrplan", "os": "Android 9"}
    mgate_ver = "1.21"
    mgate_lang = "de"
    timezone = "Europe/Berlin"

    @property
    def provider_id(self) -> str:
        return PROVIDER_SAARVV

    @property
    def provider_name(self) -> str:
        return "saarVV (Saarland)"
