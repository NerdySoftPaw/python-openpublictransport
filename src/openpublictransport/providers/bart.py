"""BART — Bay Area Rapid Transit (USA) provider.

Modern HAFAS ``mgate.exe`` gateway; unsigned. Config from
public-transport/transport-apis (``us/bart-hafas-mgate``).
"""

from ..const import PROVIDER_BART_US
from .hafas_mgate_base import HafasMgateBaseProvider


class BARTProvider(HafasMgateBaseProvider):
    """BART (San Francisco Bay Area) via HAFAS mgate."""

    mgate_endpoint = "https://planner.bart.gov/bin/mgate.exe"
    mgate_auth = {"type": "AID", "aid": "kEwHkFUCIL500dym"}
    mgate_client = {"id": "BART", "type": "WEB", "name": "webapp", "l": "vs_webapp"}
    mgate_ver = "1.40"
    timezone = "America/Los_Angeles"

    @property
    def provider_id(self) -> str:
        return PROVIDER_BART_US

    @property
    def provider_name(self) -> str:
        return "BART (San Francisco Bay Area)"
