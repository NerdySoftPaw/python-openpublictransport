"""VBN — Verkehrsverbund Bremen/Niedersachsen provider.

Modern HAFAS ``mgate.exe`` gateway; unsigned. Config from
public-transport/transport-apis (``de/vbn-hafas-mgate``). The access id is the one used by
the operator's own app, not an officially issued key, so it may be rotated
without notice.

Unlike the VBN OTP / TRIAS providers this one needs no API key.
"""

from ..const import PROVIDER_VBN_HAFAS
from .hafas_mgate_base import HafasMgateBaseProvider


class VBNHafasProvider(HafasMgateBaseProvider):
    """VBN (Bremen / Niedersachsen) via HAFAS mgate, no API key."""

    mgate_endpoint = "https://fahrplaner.vbn.de/bin/mgate.exe"
    mgate_auth = {"type": "AID", "aid": "kaoxIXLn03zCr2KR"}
    mgate_client = {"type": "IPH", "id": "VBN", "name": "vbn", "v": "6000000"}
    mgate_ver = "1.27"
    mgate_lang = "de"
    timezone = "Europe/Berlin"

    @property
    def provider_id(self) -> str:
        return PROVIDER_VBN_HAFAS

    @property
    def provider_name(self) -> str:
        return "VBN (Bremen / Niedersachsen)"
