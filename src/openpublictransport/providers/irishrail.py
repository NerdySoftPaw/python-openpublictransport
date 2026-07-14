"""Iarnród Éireann — Irish Rail (Ireland) provider.

Modern HAFAS ``mgate.exe`` gateway; unsigned. Config from
public-transport/transport-apis (``ie/iarnrod-eireann-hafas-mgate``).
"""

from ..const import PROVIDER_IRISHRAIL_IE
from .hafas_mgate_base import HafasMgateBaseProvider


class IrishRailProvider(HafasMgateBaseProvider):
    """Iarnród Éireann / Irish Rail via HAFAS mgate."""

    mgate_endpoint = "https://journeyplanner.irishrail.ie/bin/mgate.exe"
    mgate_auth = {"type": "AID", "aid": "P9bplgVCGnozdgQE"}
    mgate_client = {
        "type": "IPA",
        "id": "IRISHRAIL",
        "v": "4000100",
        "name": "IrishRailPROD-APPSTORE",
        "os": "iOS 12.4.8",
    }
    mgate_ver = "1.18"
    timezone = "Europe/Dublin"

    @property
    def provider_id(self) -> str:
        return PROVIDER_IRISHRAIL_IE

    @property
    def provider_name(self) -> str:
        return "Iarnród Éireann (Ireland)"
