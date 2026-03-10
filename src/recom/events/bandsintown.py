"""Bandsintown city events scraper — no API key required."""

from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime, timedelta, timezone

import httpx
from bs4 import BeautifulSoup

from recom.config import Settings
from recom.models import Event, EventSource, EASTERN

logger = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
TIMEOUT = 30.0
BANDSINTOWN_CITY_SLUG = "boston-ma"


def _make_id(title: str, date_str: str) -> str:
    raw = f"{title.strip().lower()}|{date_str}"
    h = hashlib.sha256(raw.encode()).hexdigest()[:12]
    return f"bandsintown_{h}"


def _parse_bit_date(date_str: str) -> datetime | None:
    """Parse Bandsintown date strings."""
    if not date_str:
        return None
    date_str = date_str.strip()
    formats = [
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%d",
        "%a, %b %d, %Y",
        "%B %d, %Y",
    ]
    for fmt in formats:
        try:
            dt = datetime.strptime(date_str, fmt)
            if date_str.endswith("Z"):
                dt = dt.replace(tzinfo=timezone.utc).astimezone(EASTERN)
            return dt
        except ValueError:
            pass
    return None


async def _fetch_via_api(settings: Settings) -> list[Event]:
    """Try the Bandsintown public discover API (no auth)."""
    now = datetime.now(timezone.utc)
    start_date = now.strftime("%Y-%m-%d")
    end_date = (now + timedelta(days=14)).strftime("%Y-%m-%d")

    # Bandsintown artist/city search uses app_id (any string works)
    url = "https://rest.bandsintown.com/events/search"
    params = {
        "location": f"{settings.latitude},{settings.longitude}",
        "radius": "25",
        "date": f"{start_date},{end_date}",
        "per_page": "50",
        "app_id": "recom_events",
    }
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}

    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        resp = await client.get(url, params=params, headers=headers)
        resp.raise_for_status()
        data = resp.json()

    events: list[Event] = []
    items = data if isinstance(data, list) else data.get("data", data.get("events", []))
    for item in items:
        title = item.get("title") or item.get("description") or ""
        if not title:
            # Build title from artists
            artists = item.get("artists") or item.get("lineup") or []
            if isinstance(artists, list):
                names = [a.get("name", a) if isinstance(a, dict) else str(a) for a in artists[:3]]
                title = ", ".join(names)
        if not title:
            continue
        # Strip " @ Venue" suffix from title (Bandsintown API includes it)
        if " @ " in title:
            title = title.split(" @ ")[0].strip()

        date_str = item.get("datetime") or item.get("starts_at") or item.get("date") or ""
        start_time = _parse_bit_date(date_str)

        venue = item.get("venue") or {}
        venue_name = venue.get("name", "") if isinstance(venue, dict) else str(venue)
        venue_city = venue.get("city", "") if isinstance(venue, dict) else ""

        artists = item.get("artists") or item.get("lineup") or []
        organizer = None
        if isinstance(artists, list) and artists:
            names = [a.get("name", a) if isinstance(a, dict) else str(a) for a in artists[:5]]
            organizer = ", ".join(names)

        events.append(Event(
            id=_make_id(title, date_str),
            source=EventSource.BANDSINTOWN,
            title=title,
            url=item.get("url") or item.get("ticket_url") or "",
            start_time=start_time,
            location_name=venue_name,
            location_address=venue_city,
            category="music",
            organizer=organizer,
            image_url=item.get("thumb_url") or item.get("image_url"),
        ))

    return events


async def _fetch_via_scrape(settings: Settings) -> list[Event]:
    """Scrape Bandsintown Boston city events page."""
    url = f"https://www.bandsintown.com/c/{BANDSINTOWN_CITY_SLUG}"
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "en-US,en;q=0.9",
    }
    now = datetime.now(timezone.utc)
    cutoff = now + timedelta(days=14)

    async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True) as client:
        resp = await client.get(url, headers=headers)
        resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    events: list[Event] = []
    seen: set[str] = set()

    # Look for JSON-LD structured data
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
            items = data if isinstance(data, list) else [data]
            for item in items:
                if item.get("@type") not in ("MusicEvent", "Event"):
                    continue
                title = item.get("name", "")
                if not title or title.lower() in seen:
                    continue
                seen.add(title.lower())

                start_raw = item.get("startDate", "")
                start_time = _parse_bit_date(start_raw)
                if start_time:
                    st = start_time
                    if st.tzinfo is None:
                        st = st.replace(tzinfo=timezone.utc)
                    if not (now <= st <= cutoff):
                        continue

                loc = item.get("location") or {}
                venue_name = loc.get("name", "") if isinstance(loc, dict) else ""
                addr = loc.get("address", {}) if isinstance(loc, dict) else {}
                venue_addr = addr.get("addressLocality", "") if isinstance(addr, dict) else str(addr)

                performers = item.get("performer") or []
                if isinstance(performers, dict):
                    performers = [performers]
                organizer = ", ".join(p.get("name", "") for p in performers[:5] if isinstance(p, dict)) or None

                events.append(Event(
                    id=_make_id(title, start_raw),
                    source=EventSource.BANDSINTOWN,
                    title=title,
                    url=item.get("url", "") or item.get("@id", ""),
                    start_time=start_time,
                    location_name=venue_name,
                    location_address=venue_addr,
                    category="music",
                    organizer=organizer,
                ))
        except Exception:
            pass

    # Fallback: look for event cards in HTML
    if not events:
        for card in soup.find_all(attrs={"data-event-id": True}):
            title_el = card.find(class_=re.compile(r"title|artist|event-name", re.I))
            title = title_el.get_text(strip=True) if title_el else ""
            if not title or title.lower() in seen:
                continue
            seen.add(title.lower())

            date_el = card.find(class_=re.compile(r"date|time", re.I))
            date_str = date_el.get_text(strip=True) if date_el else ""
            start_time = _parse_bit_date(date_str)

            venue_el = card.find(class_=re.compile(r"venue|location", re.I))
            venue = venue_el.get_text(strip=True) if venue_el else ""

            link = card.find("a", href=True)
            evt_url = link["href"] if link else ""
            if evt_url and evt_url.startswith("/"):
                evt_url = "https://www.bandsintown.com" + evt_url

            events.append(Event(
                id=_make_id(title, date_str),
                source=EventSource.BANDSINTOWN,
                title=title,
                url=evt_url,
                start_time=start_time,
                location_name=venue,
                category="music",
            ))

    return events


async def fetch_bandsintown(settings: Settings) -> list[Event]:
    """Fetch Boston music events from Bandsintown."""
    # Try API first, fall back to scraping
    try:
        events = await _fetch_via_api(settings)
        if events:
            logger.info("Bandsintown API returned %d events", len(events))
            return events
    except Exception:
        logger.debug("Bandsintown API failed, falling back to scrape")

    try:
        events = await _fetch_via_scrape(settings)
        logger.info("Bandsintown scrape returned %d events", len(events))
        return events
    except Exception:
        logger.exception("Bandsintown scrape failed")
        return []
