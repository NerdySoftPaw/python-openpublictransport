"""Hong Kong provider: KMB/LWB buses and MTR trains via data.gov.hk open APIs.

No API key. Endpoints:

* KMB/LWB stop list  ``https://data.etabus.gov.hk/v1/transport/kmb/stop``
  (~6,800 stops, ~1.5 MB, fetched once per instance and cached)
* KMB/LWB stop ETA   ``https://data.etabus.gov.hk/v1/transport/kmb/stop-eta/{stop}``
* MTR next train     ``https://rt.data.gov.hk/v1/transport/mtr/getSchedule.php?line=XXX&sta=YYY``

Stop ids encode the operator:

* ``kmb:<stop_id>``      – a KMB/LWB bus stop (16-char hex id)
* ``mtr:<LINE>:<STA>``   – one MTR line at one station, e.g. ``mtr:TWL:TST``
* ``mtr:<STA>``          – all lines serving a station, e.g. ``mtr:ADM``

The MTR line/station table is static (from the official
``mtr_lines_and_stations.csv`` of the MTR open data spec,
https://opendata.mtr.com.hk/doc/). The next-train API covers the heavy-rail
lines below; Light Rail and MTR Bus are not included.

Transport types: KMB → ``bus``. MTR → ``subway``, except Airport Express (AEL),
East Rail (EAL) and Tuen Ma (TML) → ``train``.

ETAs are realtime estimates without a separate schedule, so ``delay`` is always
0 and ``planned_time == departure_time``. KMB marks timetable-only ETAs with
``rmk_en == "Scheduled Bus"``; those are reported with ``is_realtime=False``.
KMB returns ``eta: null`` when no bus is expected (e.g. overnight) — those rows
are skipped. MTR answers ``status: 0`` / ``NT-204`` "contents are empty" outside
service hours, which is treated as "no departures".

Citybus (CTB) is not supported yet: its ETA endpoint needs route + stop
(``/v2/transport/citybus/eta/CTB/{stop}/{route}``), so a stop board would need
the route-stop mapping of every route first.
"""

import asyncio
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Union
from zoneinfo import ZoneInfo

from ..exceptions import ApiResponseError
from ..models import UnifiedDeparture
from .base import BaseProvider
from ..const import PROVIDER_HONGKONG

_LOGGER = logging.getLogger(__name__)


KMB_API_BASE = "https://data.etabus.gov.hk/v1/transport/kmb"
MTR_SCHEDULE_URL = "https://rt.data.gov.hk/v1/transport/mtr/getSchedule.php"

MTR_LINES: Dict[str, str] = {
    "AEL": "Airport Express",
    "DRL": "Disneyland Resort Line",
    "EAL": "East Rail Line",
    "ISL": "Island Line",
    "KTL": "Kwun Tong Line",
    "SIL": "South Island Line",
    "TCL": "Tung Chung Line",
    "TKL": "Tseung Kwan O Line",
    "TML": "Tuen Ma Line",
    "TWL": "Tsuen Wan Line",
}

MTR_TRAIN_LINES = frozenset({"AEL", "EAL", "TML"})

#: Stations served by each line, in line order.
MTR_LINE_STATIONS: Dict[str, List[str]] = {
    "AEL": ["HOK", "KOW", "TSY", "AIR", "AWE"],
    "DRL": ["SUN", "DIS"],
    "EAL": ["ADM", "EXC", "HUH", "MKK", "KOT", "TAW", "SHT", "FOT", "UNI", "TAP", "TWO", "FAN", "SHS", "LOW", "LMC"],
    "ISL": ["KET", "HKU", "SYP", "SHW", "CEN", "ADM", "WAC", "CAB", "TIH", "FOH", "NOP", "QUB", "TAK", "SWH", "SKW",
            "HFC", "CHW"],
    "KTL": ["WHA", "HOM", "YMT", "MOK", "PRE", "SKM", "KOT", "LOF", "WTS", "DIH", "CHH", "KOB", "NTK", "KWT", "LAT",
            "YAT", "TIK"],
    "SIL": ["ADM", "OCP", "WCH", "LET", "SOH"],
    "TCL": ["HOK", "KOW", "OLY", "NAC", "LAK", "TSY", "SUN", "TUC"],
    "TKL": ["NOP", "QUB", "YAT", "TIK", "TKO", "LHP", "HAH", "POA"],
    "TML": ["WKS", "MOS", "HEO", "TSH", "SHM", "CIO", "STW", "CKT", "TAW", "HIK", "DIH", "KAT", "SUW", "TKW", "HOM",
            "HUH", "ETS", "AUS", "NAC", "MEF", "TWW", "KSR", "YUL", "LOP", "TIS", "SIH", "TUM"],
    "TWL": ["CEN", "ADM", "TST", "JOR", "YMT", "MOK", "PRE", "SSP", "CSW", "LCK", "MEF", "LAK", "KWF", "KWH", "TWH",
            "TSW"],
}

#: Station code -> (English name, Chinese name).
MTR_STATIONS: Dict[str, tuple] = {
    "AWE": ("AsiaWorld-Expo", "博覽館"), "AIR": ("Airport", "機場"), "TSY": ("Tsing Yi", "青衣"),
    "KOW": ("Kowloon", "九龍"), "HOK": ("Hong Kong", "香港"), "SUN": ("Sunny Bay", "欣澳"),
    "DIS": ("Disneyland Resort", "迪士尼"), "LOW": ("Lo Wu", "羅湖"), "SHS": ("Sheung Shui", "上水"),
    "FAN": ("Fanling", "粉嶺"), "TWO": ("Tai Wo", "太和"), "TAP": ("Tai Po Market", "大埔墟"),
    "UNI": ("University", "大學"), "FOT": ("Fo Tan", "火炭"), "SHT": ("Sha Tin", "沙田"),
    "TAW": ("Tai Wai", "大圍"), "KOT": ("Kowloon Tong", "九龍塘"), "MKK": ("Mong Kok East", "旺角東"),
    "HUH": ("Hung Hom", "紅磡"), "EXC": ("Exhibition Centre", "會展"), "ADM": ("Admiralty", "金鐘"),
    "LMC": ("Lok Ma Chau", "落馬洲"), "CHW": ("Chai Wan", "柴灣"), "HFC": ("Heng Fa Chuen", "杏花邨"),
    "SKW": ("Shau Kei Wan", "筲箕灣"), "SWH": ("Sai Wan Ho", "西灣河"), "TAK": ("Tai Koo", "太古"),
    "QUB": ("Quarry Bay", "鰂魚涌"), "NOP": ("North Point", "北角"), "FOH": ("Fortress Hill", "炮台山"),
    "TIH": ("Tin Hau", "天后"), "CAB": ("Causeway Bay", "銅鑼灣"), "WAC": ("Wan Chai", "灣仔"),
    "CEN": ("Central", "中環"), "SHW": ("Sheung Wan", "上環"), "SYP": ("Sai Ying Pun", "西營盤"),
    "HKU": ("HKU", "香港大學"), "KET": ("Kennedy Town", "堅尼地城"), "TIK": ("Tiu Keng Leng", "調景嶺"),
    "YAT": ("Yau Tong", "油塘"), "LAT": ("Lam Tin", "藍田"), "KWT": ("Kwun Tong", "觀塘"),
    "NTK": ("Ngau Tau Kok", "牛頭角"), "KOB": ("Kowloon Bay", "九龍灣"), "CHH": ("Choi Hung", "彩虹"),
    "DIH": ("Diamond Hill", "鑽石山"), "WTS": ("Wong Tai Sin", "黃大仙"), "LOF": ("Lok Fu", "樂富"),
    "SKM": ("Shek Kip Mei", "石硤尾"), "PRE": ("Prince Edward", "太子"), "MOK": ("Mong Kok", "旺角"),
    "YMT": ("Yau Ma Tei", "油麻地"), "HOM": ("Ho Man Tin", "何文田"), "WHA": ("Whampoa", "黃埔"),
    "WKS": ("Wu Kai Sha", "烏溪沙"), "MOS": ("Ma On Shan", "馬鞍山"), "HEO": ("Heng On", "恆安"),
    "TSH": ("Tai Shui Hang", "大水坑"), "SHM": ("Shek Mun", "石門"), "CIO": ("City One", "第一城"),
    "STW": ("Sha Tin Wai", "沙田圍"), "CKT": ("Che Kung Temple", "車公廟"), "HIK": ("Hin Keng", "顯徑"),
    "KAT": ("Kai Tak", "啟德"), "SUW": ("Sung Wong Toi", "宋皇臺"), "TKW": ("To Kwa Wan", "土瓜灣"),
    "ETS": ("East Tsim Sha Tsui", "尖東"), "AUS": ("Austin", "柯士甸"), "NAC": ("Nam Cheong", "南昌"),
    "MEF": ("Mei Foo", "美孚"), "TWW": ("Tsuen Wan West", "荃灣西"), "KSR": ("Kam Sheung Road", "錦上路"),
    "YUL": ("Yuen Long", "元朗"), "LOP": ("Long Ping", "朗屏"), "TIS": ("Tin Shui Wai", "天水圍"),
    "SIH": ("Siu Hong", "兆康"), "TUM": ("Tuen Mun", "屯門"), "TUC": ("Tung Chung", "東涌"),
    "LAK": ("Lai King", "茘景"), "OLY": ("Olympic", "奧運"), "POA": ("Po Lam", "寶琳"),
    "HAH": ("Hang Hau", "坑口"), "TKO": ("Tseung Kwan O", "將軍澳"), "LHP": ("LOHAS Park", "康城"),
    "TSW": ("Tsuen Wan", "荃灣"), "TWH": ("Tai Wo Hau", "大窩口"), "KWH": ("Kwai Hing", "葵興"),
    "KWF": ("Kwai Fong", "葵芳"), "LCK": ("Lai Chi Kok", "茘枝角"), "CSW": ("Cheung Sha Wan", "長沙灣"),
    "SSP": ("Sham Shui Po", "深水埗"), "JOR": ("Jordan", "佐敦"), "TST": ("Tsim Sha Tsui", "尖沙咀"),
    "OCP": ("Ocean Park", "海洋公園"), "WCH": ("Wong Chuk Hang", "黃竹坑"), "LET": ("Lei Tung", "利東"),
    "SOH": ("South Horizons", "海怡半島"),
}


def _parse_mtr_time(value: str, tz: Any) -> Optional[datetime]:
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=tz)
    except (TypeError, ValueError):
        return None


def _parse_iso(value: str) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def mtr_station_name(code: str) -> str:
    """Return the English name of an MTR station code (the code if unknown)."""
    entry = MTR_STATIONS.get(code)
    return entry[0] if entry else code


def mtr_lines_for_station(code: str) -> List[str]:
    return [line for line, stations in MTR_LINE_STATIONS.items() if code in stations]


def _matches(words: List[str], *names: str) -> bool:
    haystacks = [n.lower() for n in names if n]
    return any(all(w in h for w in words) for h in haystacks)


class HongKongProvider(BaseProvider):
    """Hong Kong: KMB/LWB buses and MTR heavy rail (Citybus not yet supported)."""

    timezone = "Asia/Hong_Kong"
    max_search_results = 50

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._kmb_stops: Optional[List[Dict[str, Any]]] = None
        self._kmb_lock: Optional[asyncio.Lock] = None

    @property
    def provider_id(self) -> str:
        return PROVIDER_HONGKONG

    @property
    def provider_name(self) -> str:
        return "Hong Kong (KMB / MTR)"

    def get_timezone(self) -> str:
        return self.timezone

    # ------------------------------------------------------------------ search

    async def _get_kmb_stops(self) -> List[Dict[str, Any]]:
        if self._kmb_stops is not None:
            return self._kmb_stops
        if self._kmb_lock is None:
            self._kmb_lock = asyncio.Lock()
        async with self._kmb_lock:
            if self._kmb_stops is None:
                data = await self._request("get", f"{KMB_API_BASE}/stop", timeout=30, retries=2)
                if not isinstance(data, dict) or not isinstance(data.get("data"), list):
                    raise ApiResponseError(f"{self.provider_name}: unexpected KMB stop list response")
                self._kmb_stops = [s for s in data["data"] if isinstance(s, dict) and s.get("stop")]
        return self._kmb_stops

    def search_mtr(self, search_term: str) -> List[Dict[str, Any]]:
        words = search_term.lower().split()
        if not words:
            return []
        results = []
        for line, stations in MTR_LINE_STATIONS.items():
            for code in stations:
                name_en, name_tc = MTR_STATIONS.get(code, (code, ""))
                if _matches(words, name_en, name_tc) or search_term.strip().upper() == code:
                    results.append(
                        {
                            "id": f"mtr:{line}:{code}",
                            "name": f"{name_en} ({MTR_LINES[line]})",
                            "place": "Hong Kong",
                            "area_type": "stop",
                            "transport_modes": ["train" if line in MTR_TRAIN_LINES else "subway"],
                        }
                    )
        # Exact station-name matches first, then alphabetical.
        term = search_term.strip().lower()
        results.sort(key=lambda r: (r["name"].split(" (")[0].lower() != term, r["name"]))
        return results

    async def search_stops(self, search_term: str) -> List[Dict[str, Any]]:
        words = (search_term or "").lower().split()
        if not words:
            return []
        results = self.search_mtr(search_term)
        for stop in await self._get_kmb_stops():
            if len(results) >= self.max_search_results:
                break
            if _matches(words, stop.get("name_en", ""), stop.get("name_tc", "")):
                results.append(
                    {
                        "id": f"kmb:{stop['stop']}",
                        "name": stop.get("name_en") or stop.get("name_tc") or stop["stop"],
                        "place": "Hong Kong",
                        "area_type": "stop",
                        "transport_modes": ["bus"],
                    }
                )
        return results[: self.max_search_results]

    # ------------------------------------------------------------------ departures

    async def fetch_departures(
        self,
        station_id: Optional[str],
        place_dm: str,
        name_dm: str,
        departures_limit: int,
    ) -> Optional[Dict[str, Any]]:
        if not station_id:
            matches = await self.search_stops(name_dm)
            if not matches:
                return {"stopEvents": []}
            station_id = matches[0]["id"]

        operator, _, rest = str(station_id).partition(":")
        operator = operator.lower()
        if operator == "kmb" and rest:
            events = await self._fetch_kmb(rest)
            events.sort(key=lambda e: e.get("eta") or "")
        elif operator == "mtr" and rest:
            line, _, sta = rest.upper().partition(":")
            if sta:
                lines = [line]
            else:
                sta = line
                lines = mtr_lines_for_station(sta)
                if not lines:
                    raise ValueError(f"Unknown MTR station code: {sta}")
            chunks = await asyncio.gather(*(self._fetch_mtr(ln, sta) for ln in lines))
            events = [e for chunk in chunks for e in chunk]
            events.sort(key=lambda e: e.get("time") or "")
        else:
            raise ValueError(
                f"Invalid Hong Kong stop id {station_id!r}; expected 'kmb:<stop>', 'mtr:<LINE>:<STA>' or 'mtr:<STA>'"
            )
        return {"stopEvents": events[:departures_limit] if departures_limit else events}

    async def _fetch_kmb(self, stop_id: str) -> List[Dict[str, Any]]:
        data = await self._request("get", f"{KMB_API_BASE}/stop-eta/{stop_id}", timeout=15, retries=2)
        if not isinstance(data, dict):
            raise ApiResponseError(f"{self.provider_name}: unexpected KMB ETA response")
        events: List[Dict[str, Any]] = []
        seen = set()
        for row in data.get("data") or []:
            if not isinstance(row, dict) or not row.get("eta"):
                continue  # eta null: no bus expected (e.g. overnight)
            key = (row.get("route"), row.get("dir"), row.get("eta"))
            if key in seen:
                continue  # same bus listed under several service_types
            seen.add(key)
            events.append(dict(row, operator="kmb"))
        return events

    async def _fetch_mtr(self, line: str, sta: str) -> List[Dict[str, Any]]:
        data = await self._request(
            "get", MTR_SCHEDULE_URL, params={"line": line, "sta": sta}, timeout=15, retries=2
        )
        if not isinstance(data, dict):
            raise ApiResponseError(f"{self.provider_name}: unexpected MTR response")
        if str(data.get("status")) != "1":
            # status 0 + NT-204 "contents are empty": outside service hours or
            # a line/station combination without trains.
            _LOGGER.debug("MTR %s-%s: %s", line, sta, data.get("message"))
            return []
        block = (data.get("data") or {}).get(f"{line}-{sta}") or {}
        delayed = data.get("isdelay") == "Y"
        events: List[Dict[str, Any]] = []
        for direction in ("UP", "DOWN"):
            for train in block.get(direction) or []:
                if not isinstance(train, dict) or not train.get("time"):
                    continue
                if train.get("valid") not in (None, "Y"):
                    continue
                events.append(
                    dict(train, operator="mtr", line=line, sta=sta, direction=direction, isdelay=delayed)
                )
        return events

    def parse_departure(
        self, stop: Dict[str, Any], tz: Union[ZoneInfo, Any], now: datetime
    ) -> Optional[UnifiedDeparture]:
        try:
            if stop.get("operator") == "mtr":
                return self._parse_mtr(stop, tz, now)
            return self._parse_kmb(stop, tz, now)
        except Exception as e:  # noqa: BLE001
            _LOGGER.debug("Error parsing Hong Kong departure: %s", e)
            return None

    def _parse_kmb(self, row: Dict[str, Any], tz: Any, now: datetime) -> Optional[UnifiedDeparture]:
        dt = _parse_iso(row.get("eta") or "")
        if not dt:
            return None
        dep = dt.astimezone(tz)
        remark = (row.get("rmk_en") or "").strip()
        return UnifiedDeparture(
            line=str(row.get("route") or ""),
            destination=row.get("dest_en") or row.get("dest_tc") or "Unknown",
            departure_time=dep.strftime("%H:%M"),
            planned_time=dep.strftime("%H:%M"),
            delay=0,
            platform=None,
            transportation_type="bus",
            is_realtime="scheduled" not in remark.lower(),
            minutes_until_departure=max(0, int((dep - now).total_seconds() // 60)),
            departure_time_obj=dep,
            agency="KMB",
            notices=[remark] if remark else None,
        )

    def _parse_mtr(self, train: Dict[str, Any], tz: Any, now: datetime) -> Optional[UnifiedDeparture]:
        dep = _parse_mtr_time(train.get("time") or "", tz)
        if not dep:
            return None
        line = str(train.get("line") or "")
        platform = str(train.get("plat")) if train.get("plat") else None
        return UnifiedDeparture(
            line=line,
            destination=mtr_station_name(str(train.get("dest") or "")) or "Unknown",
            departure_time=dep.strftime("%H:%M"),
            planned_time=dep.strftime("%H:%M"),
            delay=0,
            platform=platform,
            transportation_type="train" if line in MTR_TRAIN_LINES else "subway",
            is_realtime=True,
            minutes_until_departure=max(0, int((dep - now).total_seconds() // 60)),
            departure_time_obj=dep,
            description=MTR_LINES.get(line),
            agency="MTR",
            notices=["Train service delayed"] if train.get("isdelay") else None,
        )
