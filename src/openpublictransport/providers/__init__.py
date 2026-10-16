"""Provider registry and factory."""

import aiohttp
from typing import Dict, Optional, Type

from ..const import (
    PROVIDER_AVV_AUGSBURG,
    PROVIDER_BEG,
    PROVIDER_BSVG,
    PROVIDER_BVG,
    PROVIDER_DB,
    PROVIDER_DING,
    PROVIDER_HVV,
    PROVIDER_HVV_GTI,
    PROVIDER_KVV,
    PROVIDER_MVV,
    PROVIDER_NTA_IE,
    PROVIDER_NVBW,
    PROVIDER_NWL,
    PROVIDER_OEBB,
    PROVIDER_OTP_CUSTOM,
    PROVIDER_RMV,
    PROVIDER_RVV,
    PROVIDER_SBB,
    PROVIDER_TRAFIKLAB_SE,
    PROVIDER_TRANSITOUS,
    PROVIDER_VAGFR,
    PROVIDER_VBN_OTP,
    PROVIDER_VBN_TRIAS,
    PROVIDER_VGN,
    PROVIDER_VRN,
    PROVIDER_VRR,
    PROVIDER_VVO,
    PROVIDER_VVS,
    PROVIDER_NATIONAL_RAIL,
    PROVIDER_REJSEPLANEN,
    PROVIDER_NS_NL,
    PROVIDER_MOBILITEIT_LU,
    PROVIDER_ENTUR_NO,
    PROVIDER_BART_US,
    PROVIDER_DART_US,
    PROVIDER_IRISHRAIL_IE,
    PROVIDER_TPG_CH,
    PROVIDER_VMV,
    PROVIDER_GVH,
    PROVIDER_NAHSH,
    PROVIDER_INSA,
    PROVIDER_VMT,
    PROVIDER_SAARVV,
    PROVIDER_VBN_HAFAS,
    PROVIDER_NVV,
    PROVIDER_VOS,
    PROVIDER_VSN,
    PROVIDER_VAO_AT,
    PROVIDER_TRIMET_US,
    PROVIDER_IRAIL_BE,
    PROVIDER_SL_SE,
    PROVIDER_TTC_CA,
    PROVIDER_HONGKONG,
    PROVIDER_WIENERLINIEN,
    PROVIDER_TFL,
    PROVIDER_TFNSW,
    PROVIDER_DIGITRANSIT,
    PROVIDER_GOLEMIO,
)
from .avv import AVVProvider
from .base import BaseProvider
from .beg import BEGProvider
from .bsvg import BSVGProvider
from .bvg import BVGProvider
from .db import DBProvider
from .ding import DINGProvider
from .hvv import HVVProvider
from .hvv_gti import HVVGTIProvider
from .kvv import KVVProvider
from .mvv import MVVProvider
from .nta import NTAProvider
from .nvbw import NVBWProvider
from .nwl import NWLProvider
from .oebb import OeBBProvider
from .otp_custom import OTPCustomProvider
from .rmv import RMVProvider
from .rvv import RVVProvider
from .sbb import SBBProvider
from .trafiklab import TrafiklabProvider
from .transitous import TransitousProvider
from .vagfr import VAGFRProvider
from .vbn import VBNOTPProvider, VBNTriasProvider
from .vgn import VGNProvider
from .vrn import VRNProvider
from .vrr import VRRProvider
from .vvo import VVOProvider
from .vvs import VVSProvider
from .national_rail import NationalRailProvider
from .rejseplanen import RejseplanenProvider
from .ns import NSProvider
from .mobiliteit_lu import MobiliteitLuProvider
from .entur import EnturProvider
from .bart import BARTProvider
from .dart import DARTProvider
from .irishrail import IrishRailProvider
from .tpg import TPGProvider
from .vmv import VMVProvider
from .gvh import GVHProvider
from .nahsh import NAHSHProvider
from .insa import INSAProvider
from .vmt import VMTProvider
from .saarvv import SaarVVProvider
from .vbn_hafas import VBNHafasProvider
from .nvv import NVVProvider
from .vos import VOSProvider
from .vsn import VSNProvider
from .vao import VAOProvider
from .trimet import TriMetProvider
from .irail import IRailProvider
from .sl import SLProvider
from .ttc import TTCProvider
from .hongkong import HongKongProvider
from .wienerlinien import WienerLinienProvider
from .tfl import TfLProvider
from .tfnsw import TfNSWProvider
from .digitransit import DigitransitProvider
from .golemio import GolemioProvider

_PROVIDER_REGISTRY: Dict[str, Type[BaseProvider]] = {}


def register_provider(provider_id: str, provider_class: Type[BaseProvider]) -> None:
    """Register a provider class."""
    _PROVIDER_REGISTRY[provider_id] = provider_class


def get_provider(
    provider_id: Optional[str],
    session: aiohttp.ClientSession,
    api_key: Optional[str] = None,
    api_key_secondary: Optional[str] = None,
    custom_url: Optional[str] = None,
) -> Optional[BaseProvider]:
    """Get a provider instance by ID."""
    if provider_id is None:
        return None
    provider_class = _PROVIDER_REGISTRY.get(provider_id)
    if provider_class:
        return provider_class(
            session,
            api_key=api_key,
            api_key_secondary=api_key_secondary,
            custom_url=custom_url,
        )
    return None


def get_all_provider_ids() -> list[str]:
    """Get all registered provider IDs."""
    return list(_PROVIDER_REGISTRY.keys())


def get_provider_class(
    provider_id: Optional[str],
) -> Optional[Type[BaseProvider]]:
    """Return the provider class without instantiating (no session needed)."""
    if provider_id is None:
        return None
    return _PROVIDER_REGISTRY.get(provider_id)


# Register all providers
register_provider(PROVIDER_VRR, VRRProvider)
register_provider(PROVIDER_KVV, KVVProvider)
register_provider(PROVIDER_HVV, HVVProvider)
register_provider(PROVIDER_BVG, BVGProvider)
register_provider(PROVIDER_MVV, MVVProvider)
register_provider(PROVIDER_VVS, VVSProvider)
register_provider(PROVIDER_VGN, VGNProvider)
register_provider(PROVIDER_VAGFR, VAGFRProvider)
register_provider(PROVIDER_RMV, RMVProvider)
register_provider(PROVIDER_TRAFIKLAB_SE, TrafiklabProvider)
register_provider(PROVIDER_NTA_IE, NTAProvider)
register_provider(PROVIDER_VRN, VRNProvider)
register_provider(PROVIDER_VVO, VVOProvider)
register_provider(PROVIDER_DING, DINGProvider)
register_provider(PROVIDER_AVV_AUGSBURG, AVVProvider)
register_provider(PROVIDER_RVV, RVVProvider)
register_provider(PROVIDER_BSVG, BSVGProvider)
register_provider(PROVIDER_NWL, NWLProvider)
register_provider(PROVIDER_NVBW, NVBWProvider)
register_provider(PROVIDER_BEG, BEGProvider)
register_provider(PROVIDER_SBB, SBBProvider)
register_provider(PROVIDER_OEBB, OeBBProvider)
register_provider(PROVIDER_TRANSITOUS, TransitousProvider)
register_provider(PROVIDER_DB, DBProvider)
register_provider(PROVIDER_VBN_OTP, VBNOTPProvider)
register_provider(PROVIDER_VBN_TRIAS, VBNTriasProvider)
register_provider(PROVIDER_OTP_CUSTOM, OTPCustomProvider)
register_provider(PROVIDER_NATIONAL_RAIL, NationalRailProvider)
register_provider(PROVIDER_REJSEPLANEN, RejseplanenProvider)
register_provider(PROVIDER_NS_NL, NSProvider)
register_provider(PROVIDER_MOBILITEIT_LU, MobiliteitLuProvider)
register_provider(PROVIDER_ENTUR_NO, EnturProvider)
register_provider(PROVIDER_BART_US, BARTProvider)
register_provider(PROVIDER_DART_US, DARTProvider)
register_provider(PROVIDER_IRISHRAIL_IE, IrishRailProvider)
register_provider(PROVIDER_TPG_CH, TPGProvider)
register_provider(PROVIDER_VMV, VMVProvider)
register_provider(PROVIDER_GVH, GVHProvider)
register_provider(PROVIDER_NAHSH, NAHSHProvider)
register_provider(PROVIDER_INSA, INSAProvider)
register_provider(PROVIDER_VMT, VMTProvider)
register_provider(PROVIDER_SAARVV, SaarVVProvider)
register_provider(PROVIDER_VBN_HAFAS, VBNHafasProvider)
register_provider(PROVIDER_NVV, NVVProvider)
register_provider(PROVIDER_VOS, VOSProvider)
register_provider(PROVIDER_VSN, VSNProvider)
register_provider(PROVIDER_VAO_AT, VAOProvider)
register_provider(PROVIDER_TRIMET_US, TriMetProvider)
register_provider(PROVIDER_IRAIL_BE, IRailProvider)
register_provider(PROVIDER_SL_SE, SLProvider)
register_provider(PROVIDER_TTC_CA, TTCProvider)
register_provider(PROVIDER_HONGKONG, HongKongProvider)
register_provider(PROVIDER_WIENERLINIEN, WienerLinienProvider)
register_provider(PROVIDER_TFL, TfLProvider)
register_provider(PROVIDER_TFNSW, TfNSWProvider)
register_provider(PROVIDER_DIGITRANSIT, DigitransitProvider)
register_provider(PROVIDER_GOLEMIO, GolemioProvider)
register_provider(PROVIDER_HVV_GTI, HVVGTIProvider)
