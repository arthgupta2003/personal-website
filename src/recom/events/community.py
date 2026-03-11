"""Community event sources — libraries, theatres, performing arts."""

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


# ── Generic HTML event scraper ───────────────────────────────────────────────

async def _scrape_events_page(
    url: str,
    source_id: str,
    venue_name: str,
    venue_address: str,
    organizer: str,
    category: str = "arts",
    base_url: str = "",
) -> list[Event]:
    """Generic scraper for event listing pages with standard HTML card patterns."""
    events: list[Event] = []

    async with httpx.AsyncClient(headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT, follow_redirects=True) as client:
        try:
            resp = await client.get(url)
            resp.raise_for_status()
        except Exception as exc:
            logger.warning("%s fetch failed: %s", venue_name, exc)
            return []

    soup = BeautifulSoup(resp.text, "html.parser")

    # Try multiple common card selectors
    cards = soup.select(
        "article, .event-card, .views-row, [class*='event-item'], "
        ".card, .listing-item, .tribe-events-calendar-list__event, "
        "[class*='program'], li[class*='event']"
    )[:40]

    for card in cards:
        title_el = card.select_one("h2, h3, h4, .title, [class*='title']")
        if not title_el:
            continue
        title = title_el.get_text(strip=True)
        if not title or len(title) < 3:
            continue

        link_el = card.select_one("a[href]")
        event_url = ""
        if link_el:
            href = link_el.get("href", "")
            if href.startswith("http"):
                event_url = href
            elif base_url:
                event_url = f"{base_url}{href}"

        date_el = card.select_one("time, .date, [class*='date']")
        start_time = None
        if date_el:
            dt_attr = date_el.get("datetime") or date_el.get_text(strip=True)
            start_time = parse_event_dt(dt_attr)

        desc_el = card.select_one("p, .description, [class*='desc'], .summary")
        description = desc_el.get_text(strip=True)[:300] if desc_el else ""

        img_el = card.select_one("img[src]")
        image_url = None
        if img_el:
            src = img_el.get("src", "")
            image_url = src if src.startswith("http") else (f"{base_url}{src}" if base_url else None)

        date_str = str(start_time.date()) if start_time else ""
        events.append(Event(
            id=make_event_id(source_id, title, date_str),
            source=EventSource.ARTSBOSTON,
            title=title,
            description=description,
            url=event_url,
            start_time=start_time,
            location_name=venue_name,
            location_address=venue_address,
            organizer=organizer,
            image_url=image_url,
            category=category,
        ))

    logger.info("%s returned %d events", venue_name, len(events))
    return events


# ── Boston Public Library ────────────────────────────────────────────────────

async def fetch_bpl_events(settings: Settings) -> list[Event]:
    """Scrape Boston Public Library events."""
    return await _scrape_events_page(
        url="https://www.bpl.org/events/",
        source_id="bpl",
        venue_name="Boston Public Library",
        venue_address="700 Boylston St, Boston, MA 02116",
        organizer="Boston Public Library",
        category="learning",
        base_url="https://www.bpl.org",
    )


# ── Brattle Theatre ─────────────────────────────────────────────────────────

async def fetch_brattle_events(settings: Settings) -> list[Event]:
    """Scrape Brattle Theatre film screenings."""
    return await _scrape_events_page(
        url="https://www.brattlefilm.org/category/calendar/",
        source_id="brattle",
        venue_name="Brattle Theatre",
        venue_address="40 Brattle St, Cambridge, MA 02138",
        organizer="Brattle Theatre",
        category="arts",
        base_url="https://www.brattlefilm.org",
    )


# ── Coolidge Corner Theatre ─────────────────────────────────────────────────

async def fetch_coolidge_events(settings: Settings) -> list[Event]:
    """Scrape Coolidge Corner Theatre events and special screenings."""
    return await _scrape_events_page(
        url="https://coolidge.org/films",
        source_id="coolidge",
        venue_name="Coolidge Corner Theatre",
        venue_address="290 Harvard St, Brookline, MA 02446",
        organizer="Coolidge Corner Theatre",
        category="arts",
        base_url="https://coolidge.org",
    )
