"""Songkick event source — REST API only (requires API key)."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import httpx

from calyx.config import Settings
from calyx.events.common import make_event_id
from calyx.models import Event, EventSource, parse_event_dt

logger = logging.getLogger(__name__)

TIMEOUT = 30.0
BOSTON_METRO_ID = 18842





async def fetch_songkick(settings: Settings) -> list[Event]:
    """Fetch live-music events from Songkick (requires API key)."""
    if not settings.songkick_api_key:
        logger.info("Songkick API key not configured — skipping")
        return []

    now = datetime.now(timezone.utc)
    min_date = now.strftime("%Y-%m-%d")
    max_date = (now + timedelta(days=14)).strftime("%Y-%m-%d")

    url = f"https://api.songkick.com/api/3.0/metro_areas/{BOSTON_METRO_ID}/calendar.json"
    params = {
        "apikey": settings.songkick_api_key,
        "min_date": min_date,
        "max_date": max_date,
        "per_page": "50",
    }

    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
    except Exception:
        logger.exception("Songkick API request failed")
        return []

    events: list[Event] = []
    results_page = data.get("resultsPage", {})
    for ev in results_page.get("results", {}).get("event", []):
        title = ev.get("displayName", "")
        if not title:
            continue

        start = ev.get("start") or {}
        date_str = start.get("datetime") or start.get("date", "")
        start_time = parse_event_dt(date_str)

        venue = ev.get("venue") or {}
        location = ev.get("location") or {}

        perf_names = [p.get("displayName", "") for p in ev.get("performance", [])]
        organizer = ", ".join(perf_names) if perf_names else None

        events.append(
            Event(
                id=make_event_id("songkick", title, date_str),
                source=EventSource.SONGKICK,
                title=title,
                url=ev.get("uri", ""),
                start_time=start_time,
                location_name=venue.get("displayName", ""),
                location_address=location.get("city", ""),
                category="music",
                organizer=organizer,
            )
        )

    logger.info("Songkick returned %d events", len(events))
    return events
