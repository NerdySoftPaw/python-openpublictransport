"""DART — Des Moines Area Rapid Transit (Iowa, USA) provider.

Modern HAFAS ``mgate.exe`` gateway; unsigned. Config from
public-transport/transport-apis (``us/dart-hafas-mgate``). Note: that catalogue
entry is titled "Dallas Area Rapid Transit", but the endpoint actually serves
**Des Moines, Iowa** (DART Central Station, DSM/WDM/Ankeny/Johnston stops) —
verified live. Iowa is Central time, so the timezone is unchanged.
"""

from ..const import PROVIDER_DART_US
from .hafas_mgate_base import HafasMgateBaseProvider


class DARTProvider(HafasMgateBaseProvider):
    """DART (Des Moines, Iowa) via HAFAS mgate."""

    mgate_endpoint = "https://dart.hafas.de/bin/mgate.exe"
    mgate_auth = {"type": "AID", "aid": "XNFGL2aSkxfDeK8N4waOZnsdJ"}
    mgate_client = {"id": "DART", "type": "WEB", "name": "webapp", "l": "vs_webapp"}
    mgate_ver = "1.35"
    timezone = "America/Chicago"

    @property
    def provider_id(self) -> str:
        return PROVIDER_DART_US

    @property
    def provider_name(self) -> str:
        return "DART (Des Moines)"
