"""Unified data models for all public transport providers."""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Optional


class UnifiedTransportType(str, Enum):
    """Unified transportation types across all providers."""

    BUS = "bus"
    TRAM = "tram"
    SUBWAY = "subway"
    TRAIN = "train"
    FERRY = "ferry"
    TAXI = "taxi"
    ON_DEMAND = "on_demand"
    UNKNOWN = "unknown"


@dataclass
class UnifiedDeparture:
    """Unified departure data structure."""

    line: str
    destination: str
    departure_time: str  # HH:MM format
    planned_time: str  # HH:MM format
    delay: int  # minutes
    platform: Optional[str]
    transportation_type: str
    is_realtime: bool
    minutes_until_departure: int
    departure_time_obj: datetime  # For internal sorting
    description: Optional[str] = None
    agency: Optional[str] = None
    notices: Optional[list[str]] = None
    planned_platform: Optional[str] = None
    platform_changed: bool = False
    line_color: Optional[str] = None
    line_text_color: Optional[str] = None

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        result = {
            "line": self.line,
            "destination": self.destination,
            "departure_time": self.departure_time,
            "planned_time": self.planned_time,
            "delay": self.delay,
            "platform": self.platform,
            "transportation_type": self.transportation_type,
            "is_realtime": self.is_realtime,
            "minutes_until_departure": self.minutes_until_departure,
        }
        if self.description:
            result["description"] = self.description
        if self.agency:
            result["agency"] = self.agency
        if self.notices:
            result["notices"] = self.notices
        if self.platform_changed:
            result["planned_platform"] = self.planned_platform
            result["platform_changed"] = True
        if self.line_color:
            result["line_color"] = self.line_color
        if self.line_text_color:
            result["line_text_color"] = self.line_text_color
        return result


@dataclass
class UnifiedStop:
    """Unified stop data structure for stop search results."""

    id: str
    name: str
    place: Optional[str] = None
    area_type: Optional[str] = None
    transport_modes: Optional[list[str]] = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        result: dict[str, Any] = {
            "id": self.id,
            "name": self.name,
        }
        if self.place:
            result["place"] = self.place
        if self.area_type:
            result["area_type"] = self.area_type
        if self.transport_modes is not None:
            result["transport_modes"] = self.transport_modes
        return result
