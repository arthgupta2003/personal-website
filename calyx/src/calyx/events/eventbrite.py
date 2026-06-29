"""Eventbrite event source — API with token, scrape fallback."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import httpx
from bs4 import BeautifulSoup

from calyx.config import Settings
from calyx.events.common import make_event_id
from calyx.models import Event, EventSource, parse_event_dt

logger = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
TIMEOUT = 30.0





# ── API path ──────────────────────────────────────────────────────────────────

async def _fetch_via_api(settings: Settings) -> list[Event]:
    """Use Eventbrite REST API v3 with an OAuth token."""
    now = datetime.now(timezone.utc)
    start_date = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    end_date = (now + timedelta(days=14)).strftime("%Y-%m-%dT%H:%M:%SZ")

    params: dict[str, str | float] = {
        "location.latitude": settings.latitude,
        "location.longitude": settings.longitude,
        "location.within": "25mi",
        "start_date.range_start": start_date,
        "start_date.range_end": end_date,
        "expand": "venue,organizer",
    }
    headers = {
        "Authorization": f"Bearer {settings.eventbrite_token}",
        "User-Agent": USER_AGENT,
    }

    events: list[Event] = []
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        resp = await client.get(
            "https://www.eventbriteapi.com/v3/events/search/",
            params=params,
            headers=headers,
        )
        resp.raise_for_status()
        data = resp.json()

    for ev in data.get("events", []):
        title = ev.get("name", {}).get("text", "")
        if not title:
            continue
        start_raw = ev.get("start", {}).get("utc", "")
        end_raw = ev.get("end", {}).get("utc", "")
        venue = ev.get("venue") or {}
        addr = venue.get("address") or {}
        organizer = ev.get("organizer") or {}

        events.append(
            Event(
                id=make_event_id("eventbrite", title,start_raw),
                source=EventSource.EVENTBRITE,
                title=title,
                description=(ev.get("description", {}).get("text", "") or "")[:500],
                url=ev.get("url", ""),
                start_time=parse_event_dt(start_raw),
                end_time=parse_event_dt(end_raw),
                location_name=venue.get("name", ""),
                location_address=addr.get("localized_address_display", ""),
                is_online=ev.get("online_event", False),
                price=(ev.get("ticket_availability") or {}).get("minimum_ticket_price", {}).get("display", None),
                image_url=(ev.get("logo") or {}).get("url"),
                organizer=organizer.get("name"),
                category=ev.get("category", {}).get("name") if ev.get("category") else None,
            )
        )
    return events


# ── Scrape fallback ───────────────────────────────────────────────────────────

async def _fetch_via_scrape(settings: Settings) -> list[Event]:
    """Scrape the public Eventbrite search page via its JSON-LD ItemList.

    The HTML card markup changes constantly, but Eventbrite embeds a stable
    schema.org ItemList of Events (with startDate/endDate/location) in a
    <script type="application/ld+json"> block — drive off that instead.
    """
    import json
    import re

    url = "https://www.eventbrite.com/d/ma--cambridge/events/"
    headers = {"User-Agent": USER_AGENT}

    async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True) as client:
        resp = await client.get(url, headers=headers)
        resp.raise_for_status()

    now = datetime.now(timezone.utc)
    cutoff = now + timedelta(days=14)
    events: list[Event] = []

    for block in re.findall(
        r'<script type="application/ld\+json">(.*?)</script>', resp.text, re.DOTALL
    ):
        try:
            data = json.loads(block)
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict):
            continue
        for entry in data.get("itemListElement", []):
            item = entry.get("item", {}) if isinstance(entry, dict) else {}
            title = (item.get("name") or "").strip()
            if not title:
                continue
            start_raw = item.get("startDate") or ""
            start_time = parse_event_dt(start_raw)
            if not start_time:
                continue
            start_aware = start_time if start_time.tzinfo else start_time.replace(tzinfo=timezone.utc)
            if start_aware < now.replace(hour=0, minute=0) or start_aware > cutoff:
                continue

            loc = item.get("location") or {}
            if isinstance(loc, dict):
                location_name = loc.get("name") or ""
                addr = loc.get("address")
                if isinstance(addr, dict):
                    location_address = addr.get("streetAddress") or addr.get("addressLocality") or ""
                else:
                    location_address = addr or ""
            else:
                location_name, location_address = str(loc), ""

            image = item.get("image")
            image_url = image if isinstance(image, str) else None

            events.append(
                Event(
                    id=make_event_id("eventbrite", title, start_raw),
                    source=EventSource.EVENTBRITE,
                    title=title,
                    description=(item.get("description") or "")[:500],
                    url=item.get("url") or "",
                    start_time=start_time,
                    end_time=parse_event_dt(item.get("endDate") or ""),
                    location_name=location_name,
                    location_address=location_address,
                    is_online=item.get("eventAttendanceMode") == "https://schema.org/OnlineEventAttendanceMode",
                    image_url=image_url,
                )
            )

    return events


# ── Public entry point ────────────────────────────────────────────────────────

async def fetch_eventbrite(settings: Settings) -> list[Event]:
    """Fetch events from Eventbrite (API if token available, otherwise scrape)."""
    if settings.eventbrite_token:
        try:
            logger.info("Fetching Eventbrite events via API")
            api_events = await _fetch_via_api(settings)
            if api_events:
                return api_events
            logger.info("Eventbrite API returned nothing, falling back to scrape")
        except Exception:
            logger.warning("Eventbrite API failed, falling back to scrape", exc_info=True)

    try:
        logger.info("Fetching Eventbrite events via JSON-LD scrape")
        return await _fetch_via_scrape(settings)
    except Exception:
        logger.exception("Eventbrite fetch failed")
        return []
