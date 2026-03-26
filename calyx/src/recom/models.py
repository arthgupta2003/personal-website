from __future__ import annotations

import math
from datetime import datetime, timezone
from enum import Enum
from zoneinfo import ZoneInfo

from pydantic import BaseModel


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in km between two lat/lon points."""
    R = 6371
    dLat = math.radians(lat2 - lat1)
    dLon = math.radians(lon2 - lon1)
    a = math.sin(dLat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dLon / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

EASTERN = ZoneInfo("America/New_York")


def parse_event_dt(raw: str | None) -> datetime | None:
    """Parse event datetime string, converting UTC to Eastern."""
    if not raw:
        return None
    for fmt in (
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d",
    ):
        try:
            dt = datetime.strptime(raw.strip(), fmt)
            if raw.strip().endswith("Z"):
                dt = dt.replace(tzinfo=timezone.utc).astimezone(EASTERN)
            return dt
        except ValueError:
            continue
    return None


class ActivityItem(BaseModel):
    source: str  # "youtube", "spotify", "newsletter"
    title: str
    category: str | None = None
    description: str | None = None
    timestamp: datetime | None = None
    url: str | None = None


class RawActivity(BaseModel):
    youtube: list[ActivityItem] = []
    spotify: list[ActivityItem] = []
    newsletters: list[ActivityItem] = []


class Interest(BaseModel):
    topic: str
    confidence: float  # 0.0 - 1.0
    source_signals: list[str] = []


class InterestProfile(BaseModel):
    interests: list[Interest] = []
    summary: str = ""
    generated_at: datetime = datetime.now()


class EventSource(str, Enum):
    EVENTBRITE = "eventbrite"
    MEETUP = "meetup"
    LUMA = "luma"
    SONGKICK = "songkick"
    MIT = "mit"
    HARVARD = "harvard"
    BOSTON_CALENDAR = "boston_calendar"
    DO617 = "do617"
    ARTSBOSTON = "artsboston"
    NEWSLETTER = "newsletter"
    TIMEOUT_BOSTON = "timeout_boston"
    BANDSINTOWN = "bandsintown"
    DICE = "dice"
    RESIDENT_ADVISOR = "resident_advisor"


class Event(BaseModel):
    id: str
    source: EventSource
    title: str
    description: str = ""
    url: str = ""
    start_time: datetime | None = None
    end_time: datetime | None = None
    location_name: str = ""
    location_address: str = ""
    is_online: bool = False
    price: str | None = None
    attendee_count: int | None = None
    category: str | None = None
    organizer: str | None = None
    image_url: str | None = None
    lat: float | None = None
    lon: float | None = None


class RankedEvent(BaseModel):
    event: Event
    score: float = 0
    interest_score: float = 0
    social_score: float = 0
    urgency_score: float = 0
    logistics_score: float = 0
    friend_score: float = 0
    discovery_score: float = 0
    quality_score: float = 0
    vibe: str = "mixed"  # "social", "intellectual", "mixed"
    match_reason: str = ""
    keep: bool = True
    filter_reason: str | None = None
    event_type: str = "event"  # "event", "club", "class"


class CostRecord(BaseModel):
    call_type: str  # "interest_extraction", "event_ranking", "newsletter_extraction"
    model: str
    tokens_in: int
    tokens_out: int
    cost_usd: float


class SourceStat(BaseModel):
    source_name: str
    events_found: int
    error_message: str | None = None
