"""Common parsing utilities for all providers."""

import logging
import re
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional, Union
from zoneinfo import ZoneInfo

from .models import UnifiedDeparture

_LOGGER = logging.getLogger(__name__)

# Human-readable platform labels APIs prefix onto the bare track identifier:
# "Gleis 3", "Bstg. 12", "Steig B", "Platform 4", "Pl. 2", "Track 1".
_PLATFORM_LABEL_RE = re.compile(
    r"^(?:gleis|bstg\.?|bahnsteig|steig|platform|plattform|pl\.|track|quai|voie|spor|perron)\s*",
    re.IGNORECASE,
)


def normalize_platform(value: Any) -> str:
    """Reduce a platform value to its bare identifier, lowercased.

    ``"Gleis 3"``, ``"gleis 3"`` and ``"3"`` all normalize to ``"3"``. Used to
    compare a technical platform value against a human-readable one without
    reporting a spurious platform change, and re-used by consumers that want to
    match user input like ``platform: 3`` against whatever the provider returns.
    """
    if value is None:
        return ""
    text = str(value).strip()
    if not text:
        return ""
    return _PLATFORM_LABEL_RE.sub("", text).strip().casefold()


def _parse_dt(s: str) -> Optional[datetime]:
    """Parse an ISO datetime string, returning None on failure."""
    try:
        return datetime.fromisoformat(s)
    except (ValueError, TypeError):
        return None


def _default_planned_platform_fn(stop: Dict[str, Any]) -> str:
    """Legacy planned-platform lookup used when a provider supplies none."""
    platform = stop.get("platform")
    planned_name = platform.get("plannedName") if isinstance(platform, dict) else None
    return stop.get("plannedPlatformName") or planned_name or ""


def parse_departure_generic(
    stop: Dict[str, Any],
    tz: Union[ZoneInfo, Any],
    now: datetime,
    get_transport_type_fn: Callable[[Dict[str, Any]], str],
    get_platform_fn: Callable[[Dict[str, Any]], str],
    get_realtime_fn: Callable[[Dict[str, Any], Optional[str], Optional[str]], bool],
    get_planned_platform_fn: Optional[Callable[[Dict[str, Any]], str]] = None,
    get_platform_name_fn: Optional[Callable[[Dict[str, Any]], str]] = None,
) -> Optional[UnifiedDeparture]:
    """Generic parser for departure data — shared logic across all providers."""
    try:
        if not isinstance(stop, dict):
            _LOGGER.debug("Invalid stop data: expected dict, got %s", type(stop))
            return None

        planned_time_str = stop.get("departureTimePlanned")
        estimated_time_str = stop.get("departureTimeEstimated")

        if not planned_time_str:
            _LOGGER.debug("Missing departureTimePlanned in stop data")
            return None

        if not isinstance(planned_time_str, str):
            _LOGGER.debug("Invalid departureTimePlanned: expected str, got %s", type(planned_time_str))
            return None

        planned_time = _parse_dt(planned_time_str)
        estimated_time = _parse_dt(estimated_time_str) if estimated_time_str else planned_time

        if not planned_time:
            _LOGGER.debug("Failed to parse departureTimePlanned: %s", planned_time_str)
            return None

        try:
            planned_local = planned_time.astimezone(tz)
            estimated_local = estimated_time.astimezone(tz) if estimated_time else planned_local
        except (ValueError, TypeError) as e:
            _LOGGER.debug("Failed to convert timezone: %s", e)
            return None

        delay_minutes = int((estimated_local - planned_local).total_seconds() / 60)

        transportation = stop.get("transportation", {})
        if not isinstance(transportation, dict):
            _LOGGER.debug("Invalid transportation data: expected dict, got %s", type(transportation))
            transportation = {}

        destination_obj = transportation.get("destination", {})
        if not isinstance(destination_obj, dict):
            destination_obj = {}
        destination = destination_obj.get("name", "Unknown")

        line_number = str(transportation.get("number", ""))
        description = str(transportation.get("description", ""))
        agency = stop.get("agency")

        transport_type = get_transport_type_fn(transportation)
        platform = get_platform_fn(stop)
        platform_name = get_platform_name_fn(stop) if get_platform_name_fn else ""

        time_diff = estimated_local - now
        minutes_until = max(0, int(time_diff.total_seconds() / 60))

        is_realtime = get_realtime_fn(stop, estimated_time_str, planned_time_str)

        notices = []
        for info in stop.get("infos", []):
            if isinstance(info, dict):
                text = info.get("subtitle") or info.get("title") or info.get("content", "")
                if text and isinstance(text, str):
                    notices.append(text.strip())
        for hint in stop.get("hints", []):
            if isinstance(hint, dict):
                text = hint.get("content") or hint.get("text", "")
                if text and isinstance(text, str):
                    notices.append(text.strip())

        planned_fn = get_planned_platform_fn or _default_planned_platform_fn
        planned_platform = planned_fn(stop)
        # Compare normalized values: providers mix the technical identifier
        # ("3") with the human-readable label ("Gleis 3") across the actual and
        # planned fields, and those are the same platform, not a change.
        platform_changed = bool(
            planned_platform and platform and normalize_platform(planned_platform) != normalize_platform(platform)
        )

        return UnifiedDeparture(
            line=line_number,
            destination=destination,
            departure_time=estimated_local.strftime("%H:%M"),
            planned_time=planned_local.strftime("%H:%M"),
            delay=delay_minutes,
            platform=platform,
            transportation_type=transport_type,
            is_realtime=is_realtime,
            minutes_until_departure=minutes_until,
            departure_time_obj=estimated_local,
            description=description if description else None,
            agency=agency if agency else None,
            notices=notices if notices else None,
            planned_platform=str(planned_platform).strip() if planned_platform and platform_changed else None,
            platform_changed=platform_changed,
            platform_name=str(platform_name).strip() or None,
        )

    except Exception as e:
        _LOGGER.debug("Error parsing departure: %s", e)
        return None
