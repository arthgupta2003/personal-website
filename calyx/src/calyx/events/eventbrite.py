"""Eventbrite event source — API with token, scrape fallback."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import httpx
from bs4 import BeautifulSoup

from recom.config import Settings
from recom.events.common import make_event_id
from recom.models import Event, EventSource, parse_event_dt

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
    """Scrape the public Eventbrite search page."""
    url = f"https://www.eventbrite.com/d/ma--cambridge/events/"
    headers = {"User-Agent": USER_AGENT}

    events: list[Event] = []
    async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True) as client:
        resp = await client.get(url, headers=headers)
        resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "lxml")

    # Eventbrite renders event cards in various class schemes; try common selectors
    cards = soup.select("div.search-event-card-wrapper, section.event-card-details, article[data-testid]")
    if not cards:
        # broader fallback
        cards = soup.select("a[data-event-id], div[data-testid='event-card']")

    now = datetime.now(timezone.utc)
    cutoff = now + timedelta(days=14)

    for card in cards:
        link_tag = card.find("a", href=True)
        event_url = link_tag["href"] if link_tag else ""
        if event_url and not event_url.startswith("http"):
            event_url = "https://www.eventbrite.com" + event_url

        title_tag = card.find(["h2", "h3", "p"], class_=lambda c: c and "event-card__title" in c) or card.find(["h2", "h3"])
        title = title_tag.get_text(strip=True) if title_tag else ""
        if not title:
            continue

        date_tag = card.find("p", class_=lambda c: c and "date" in str(c).lower()) or card.find("time")
        date_str = ""
        if date_tag:
            date_str = date_tag.get("datetime", "") or date_tag.get_text(strip=True)
        start_time = parse_event_dt(date_str)

        if start_time and start_time > cutoff:
            continue

        location_tag = card.find("p", class_=lambda c: c and "location" in str(c).lower())
        location = location_tag.get_text(strip=True) if location_tag else ""

        img_tag = card.find("img", src=True)
        image_url = img_tag["src"] if img_tag else None

        events.append(
            Event(
                id=make_event_id("eventbrite", title,date_str),
                source=EventSource.EVENTBRITE,
                title=title,
                url=event_url,
                start_time=start_time,
                location_name=location,
                image_url=image_url,
            )
        )

    return events


# ── Public entry point ────────────────────────────────────────────────────────

async def fetch_eventbrite(settings: Settings) -> list[Event]:
    """Fetch events from Eventbrite (API if token available, otherwise scrape)."""
    try:
        if settings.eventbrite_token:
            logger.info("Fetching Eventbrite events via API")
            return await _fetch_via_api(settings)
        else:
            logger.info("Fetching Eventbrite events via scraping (no token)")
            return await _fetch_via_scrape(settings)
    except Exception:
        logger.exception("Eventbrite fetch failed")
        return []
