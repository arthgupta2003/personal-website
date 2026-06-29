"""Berklee College of Music event source.

Berklee runs a Drupal Views calendar at https://www.berklee.edu/events. The plain
HTML page is server-rendered and paginates via ?page=N (10 events/page, sorted
ascending by date), with each event exposing a machine-readable <time datetime=...>
attribute plus structured venue/address fields. We page forward until events run
past our look-ahead window. Berklee hosts a dense stream of (mostly free) student
and faculty concerts, so this is a high-yield music source.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

import httpx
from bs4 import BeautifulSoup

from calyx.config import Settings
from calyx.events.common import make_event_id
from calyx.models import Event, EventSource, parse_event_dt

logger = logging.getLogger(__name__)

BASE = "https://www.berklee.edu"
EVENTS_URL = f"{BASE}/events"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
TIMEOUT = 30.0
MAX_PAGES = 16          # safety cap (~160 events); loop also stops once past the window
LOOKAHEAD_DAYS = 30


def _txt(row, selector: str) -> str:
    el = row.select_one(selector)
    return el.get_text(" ", strip=True) if el else ""


def _abs_url(href: str | None) -> str:
    if not href:
        return ""
    if href.startswith("http"):
        return href
    return f"{BASE}{href}"


def _parse_row(row) -> Event | None:
    title = _txt(row, ".title")
    if not title:
        return None

    link = row.select_one("a[href]")
    url = _abs_url(link["href"] if link else None)

    # daterange exposes start (and optional end) as <time datetime="ISO">
    times = [t.get("datetime") for t in row.select("time") if t.get("datetime")]
    start_time = parse_event_dt(times[0]) if times else None
    if not start_time:
        return None
    end_time = parse_event_dt(times[1]) if len(times) > 1 else None

    venue = _txt(row, ".field--name-field-event-venue-title")
    address = _txt(row, ".venue-address")

    img = row.select_one("img[src]")
    image_url = _abs_url(img["src"]) if img else None

    return Event(
        id=make_event_id("berklee", title, times[0]),
        source=EventSource.BERKLEE,
        title=title,
        description="",
        url=url,
        start_time=start_time,
        end_time=end_time,
        location_name=venue or "Berklee College of Music",
        location_address=address or "Boston, MA",
        organizer="Berklee College of Music",
        image_url=image_url,
    )


async def fetch_berklee(settings: Settings) -> list[Event]:
    """Scrape Berklee's events calendar, paging until past the look-ahead window."""
    now = datetime.now(timezone.utc)
    cutoff = now + timedelta(days=LOOKAHEAD_DAYS)
    headers = {"User-Agent": USER_AGENT}

    events: list[Event] = []
    seen: set[str] = set()

    async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True) as client:
        for page in range(MAX_PAGES):
            if page > 0:
                await asyncio.sleep(0.5)
            try:
                resp = await client.get(EVENTS_URL, params={"page": page}, headers=headers)
                resp.raise_for_status()
            except Exception:
                logger.warning("Berklee page %d request failed", page, exc_info=True)
                break

            rows = BeautifulSoup(resp.text, "lxml").select("div.views-row")
            if not rows:
                break

            page_past_cutoff = True
            for row in rows:
                evt = _parse_row(row)
                if not evt or evt.id in seen:
                    continue
                start_aware = (
                    evt.start_time
                    if evt.start_time.tzinfo
                    else evt.start_time.replace(tzinfo=timezone.utc)
                )
                if start_aware < now:
                    continue
                if start_aware <= cutoff:
                    page_past_cutoff = False
                    seen.add(evt.id)
                    events.append(evt)

            # Events are sorted ascending; once an entire page is beyond the
            # window there's nothing useful on later pages.
            if page_past_cutoff:
                break

    logger.info("Berklee returned %d events", len(events))
    return events
