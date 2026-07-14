"""Provider IDs, transport type mappings and API base URLs."""

# Provider IDs
PROVIDER_VRR = "vrr"
PROVIDER_KVV = "kvv"
PROVIDER_HVV = "hvv"
PROVIDER_BVG = "bvg"
PROVIDER_MVV = "mvv"
PROVIDER_VVS = "vvs"
PROVIDER_VGN = "vgn"
PROVIDER_VAGFR = "vagfr"
PROVIDER_RMV = "rmv"
PROVIDER_TRAFIKLAB_SE = "trafiklab_se"
PROVIDER_NTA_IE = "nta_ie"
PROVIDER_VRN = "vrn"
PROVIDER_VVO = "vvo"
PROVIDER_DING = "ding"
PROVIDER_AVV_AUGSBURG = "avv_augsburg"
PROVIDER_RVV = "rvv"
PROVIDER_BSVG = "bsvg"
PROVIDER_NWL = "nwl"
PROVIDER_NVBW = "nvbw"
PROVIDER_BEG = "beg"
PROVIDER_SBB = "sbb"
PROVIDER_OEBB = "oebb"
PROVIDER_TRANSITOUS = "transitous"
PROVIDER_DB = "db"
PROVIDER_VBN_OTP = "vbn_otp"
PROVIDER_VBN_TRIAS = "vbn_trias"
PROVIDER_OPT = "openpublictransport"
PROVIDER_OTP_CUSTOM = "otp_custom"
PROVIDER_NATIONAL_RAIL = "national_rail"
PROVIDER_REJSEPLANEN = "rejseplanen"
PROVIDER_NS_NL = "ns_nl"
PROVIDER_MOBILITEIT_LU = "mobiliteit_lu"
PROVIDER_ENTUR_NO = "entur_no"
PROVIDER_BART_US = "bart_us"
PROVIDER_DART_US = "dart_us"
PROVIDER_IRISHRAIL_IE = "irishrail_ie"
PROVIDER_TPG_CH = "tpg_ch"

# API base URLs
API_BASE_URL_VRR = "https://openservice-test.vrr.de/static03/XML_DM_REQUEST"
API_BASE_URL_KVV = "https://projekte.kvv-efa.de/sl3-alone/XSLT_DM_REQUEST"
API_BASE_URL_HVV = "https://hvv.efa.de/efa/XML_DM_REQUEST"
API_BASE_URL_TRAFIKLAB = "https://realtime-api.trafiklab.se/v1"
API_BASE_URL_NTA_GTFSR = "https://api.nationaltransport.ie/gtfsr"

# Transport type mappings
KVV_TRANSPORTATION_TYPES = {
    1: "train",
    4: "tram",
    5: "bus",
    6: "bus",  # Regionalbus (e.g. KVV line 106)
    7: "bus",  # Schnellbus
}

HVV_TRANSPORTATION_TYPES = {
    0: "train",
    1: "train",
    2: "subway",
    3: "bus",
    4: "tram",
    5: "bus",
    6: "ferry",
    7: "on_demand",
}

TRAFIKLAB_TRANSPORTATION_TYPES = {
    "BUS": "bus",
    "TRAIN": "train",
    "TRAM": "tram",
    "METRO": "subway",
    "FERRY": "ferry",
}

NTA_TRANSPORTATION_TYPES = {
    0: "tram",
    1: "subway",
    2: "train",
    3: "bus",
    4: "ferry",
    5: "tram",
    6: "tram",
    7: "train",
}
