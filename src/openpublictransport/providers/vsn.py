"""VSN — Verkehrsverbund Süd-Niedersachsen provider.

Modern HAFAS ``mgate.exe`` gateway with mic/mac request signing. Config from
public-transport/transport-apis (``de/vsn-hafas-mgate``). The access id is the one used by
the operator's own app, not an officially issued key, so it may be rotated
without notice.
"""

from ..const import PROVIDER_VSN
from .hafas_mgate_base import HafasMgateBaseProvider


class VSNProvider(HafasMgateBaseProvider):
    """VSN (Göttingen / Südniedersachsen) via HAFAS mgate."""

    mgate_endpoint = "https://fahrplaner.vsninfo.de/hafas/mgate.exe"
    mgate_auth = {"type": "AID", "aid": "Mpf5UPC0DmzV8jkg"}
    mgate_client = {"type": "IPA", "id": "VSN", "name": "vsn", "v": "5030100", "os": "iOS 13.3"}
    mgate_ver = "1.27"
    mgate_lang = "de"
    mic_mac_salt = "535033316D4275665379434C6D4E7870"
    timezone = "Europe/Berlin"

    @property
    def provider_id(self) -> str:
        return PROVIDER_VSN

    @property
    def provider_name(self) -> str:
        return "VSN (Südniedersachsen)"
