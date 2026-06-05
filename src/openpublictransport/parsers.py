"""Common parsing utilities for all providers."""

import logging
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional, Union
from zoneinfo import ZoneInfo

from .models import UnifiedDeparture

_LOGGER = logging.getLogger(__name__)


def _parse_dt(s: str) -> Optional[datetime]:
    """Parse an ISO datetime string, returning None on failure."""
    try:
        return datetime.fromisoformat(s)
    except (ValueError, TypeError):
        return None


def parse_departure_generic(
    stop: Dict[str, Any],
    tz: Union[ZoneInfo, Any],
    now: datetime,
    get_transport_type_fn: Callable[[Dict[str, Any]], str],
    get_platform_fn: Callable[[Dict[str, Any]], str],
    get_realtime_fn: Callable[[Dict[str, Any], Optional[str], Optional[str]], bool],
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

        planned_platform = stop.get("plannedPlatformName") or stop.get("platform", {}).get("plannedName")
        actual_platform = platform
        platform_changed = bool(
            planned_platform and actual_platform and str(planned_platform).strip() != str(actual_platform).strip()
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
        )

    except Exception as e:
        _LOGGER.debug("Error parsing departure: %s", e)
        return None
