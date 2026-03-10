"""University event sources — MIT and Harvard calendar scrapers."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from datetime import datetime, timedelta, timezone

import httpx
from bs4 import BeautifulSoup

from recom.config import Settings
from recom.models import Event, EventSource, parse_event_dt

logger = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
TIMEOUT = 30.0


def _make_id(source: str, title: str, date_str: str) -> str:
    raw = f"{title.strip().lower()}|{date_str}"
    h = hashlib.sha256(raw.encode()).hexdigest()[:12]
    return f"{source}_{h}"




def _try_parse_month_day_year(s: str) -> datetime | None:
    """Try several month-day-year formats on *s*."""
    for fmt in ("%B %d, %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(s.strip(), fmt)
        except ValueError:
            continue
    return None


# ── MIT Events ────────────────────────────────────────────────────────────────

async def _fetch_mit(settings: Settings) -> list[Event]:
    """Scrape MIT Events calendar."""
    now = datetime.now(timezone.utc)
    today = now.strftime("%Y/%m/%d")
    url = f"https://calendar.mit.edu/calendar/day/{today}"
    headers = {"User-Agent": USER_AGENT}

    events: list[Event] = []
    async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True) as client:
        # Fetch multiple days (up to 10)
        for day_offset in range(0, 10, 2):  # sample every other day to reduce requests
            if day_offset > 0:
                await asyncio.sleep(1.5)  # avoid rate limiting
            target = now + timedelta(days=day_offset)
            day_url = f"https://calendar.mit.edu/calendar/day/{target.strftime('%Y/%m/%d')}"
            try:
                resp = await client.get(day_url, headers=headers)
                resp.raise_for_status()
            except Exception:
                logger.warning("MIT calendar request failed for %s", day_url, exc_info=True)
                continue

            soup = BeautifulSoup(resp.text, "lxml")

            # MIT calendar uses various card structures
            cards = soup.select(
                "div.em-card, "
                "div.em-mini-card, "
                "div[class*='event-node'], "
                "article.event"
            )
            if not cards:
                # broader fallback
                cards = soup.select("div.em-item, li.em-item, div[class*='vevent']")

            for card in cards:
                link_tag = card.find("a", href=True)
                event_url = ""
                if link_tag:
                    href = link_tag["href"]
                    event_url = href if href.startswith("http") else f"https://calendar.mit.edu{href}"

                title_tag = (
                    card.find(class_=lambda c: c and "title" in str(c).lower())
                    or card.find(["h3", "h2", "h4"])
                )
                title = title_tag.get_text(strip=True) if title_tag else ""
                if not title:
                    continue

                date_tag = card.find("time") or card.find(class_=lambda c: c and "date" in str(c).lower())
                date_str = ""
                if date_tag:
                    date_str = date_tag.get("datetime", "") or date_tag.get_text(strip=True)
                start_time = parse_event_dt(date_str)
                if not start_time:
                    # Try regex extraction from the date element text
                    date_text = date_tag.get_text(strip=True) if date_tag else ""
                    m = re.search(
                        r"((?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
                        r"Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
                        r"\s+\d{1,2})",
                        date_text,
                    )
                    if m:
                        year = datetime.now().year
                        start_time = _try_parse_month_day_year(f"{m.group(1)}, {year}")
                if not start_time:
                    # Use the page date as fallback
                    start_time = target.replace(hour=12, minute=0, second=0, microsecond=0)

                location_tag = card.find(class_=lambda c: c and ("location" in str(c).lower() or "venue" in str(c).lower()))
                location = location_tag.get_text(strip=True) if location_tag else ""

                desc_tag = card.find(class_=lambda c: c and "description" in str(c).lower()) or card.find("p")
                description = desc_tag.get_text(strip=True)[:500] if desc_tag else ""

                img_tag = card.find("img", src=True)
                image_url = img_tag["src"] if img_tag else None
                if image_url and not image_url.startswith("http"):
                    image_url = f"https://calendar.mit.edu{image_url}"

                events.append(
                    Event(
                        id=_make_id("mit", title, date_str),
                        source=EventSource.MIT,
                        title=title,
                        description=description,
                        url=event_url,
                        start_time=start_time,
                        location_name=location or "MIT Campus",
                        location_address="Cambridge, MA",
                        organizer="MIT",
                        image_url=image_url,
                    )
                )

    logger.info("MIT calendar returned %d events", len(events))
    return events


# ── Harvard Events (Trumba JSON API) ─────────────────────────────────────────

async def _fetch_harvard(settings: Settings) -> list[Event]:
    """Fetch Harvard Gazette events from the Trumba JSON calendar API."""
    url = "https://www.trumba.com/calendars/gazette.json"
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}

    events: list[Event] = []
    now = datetime.now(timezone.utc)
    cutoff = now + timedelta(days=10)

    async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True) as client:
        resp = await client.get(url, headers=headers)
        resp.raise_for_status()

    data = resp.json()
    if not isinstance(data, list):
        logger.warning("Harvard Trumba API returned unexpected type: %s", type(data))
        return events

    for item in data:
        # Skip canceled events
        if item.get("canceled"):
            continue

        title = (item.get("title") or "").strip()
        if not title:
            continue

        # Parse start/end datetimes (ISO format like "2026-03-08T15:00:00")
        start_raw = item.get("startDateTime")
        end_raw = item.get("endDateTime")
        start_time = parse_event_dt(start_raw)
        end_time = parse_event_dt(end_raw)

        # Filter: only events starting within the next 10 days
        if start_time:
            # Make naive datetimes comparable with UTC now
            start_aware = (
                start_time if start_time.tzinfo else start_time.replace(tzinfo=timezone.utc)
            )
            if start_aware < now or start_aware > cutoff:
                continue
        else:
            # Skip events with no parseable start time
            continue

        # Extract plain-text location from HTML location field
        location_html = item.get("location") or ""
        if "<" in location_html:
            location = BeautifulSoup(location_html, "html.parser").get_text(strip=True)
        else:
            location = location_html.strip()

        description = (item.get("description") or "")[:500]
        event_url = item.get("permaLinkUrl") or ""

        # Image
        detail_image = item.get("detailImage") or {}
        image_url = detail_image.get("url") if isinstance(detail_image, dict) else None

        # Categories from customFields
        category = None
        custom_fields = item.get("customFields")
        if isinstance(custom_fields, list):
            for cf in custom_fields:
                if isinstance(cf, dict) and "category" in (cf.get("fieldName") or "").lower():
                    category = cf.get("value")
                    break

        date_str = start_raw or ""
        events.append(
            Event(
                id=_make_id("harvard", title, date_str),
                source=EventSource.HARVARD,
                title=title,
                description=description,
                url=event_url,
                start_time=start_time,
                end_time=end_time,
                location_name=location or "Harvard Campus",
                location_address="Cambridge, MA",
                organizer="Harvard University",
                category=category,
                image_url=image_url,
            )
        )

    logger.info("Harvard Trumba API returned %d events", len(events))
    return events


# ── Public entry point ────────────────────────────────────────────────────────

async def fetch_university_events(settings: Settings) -> list[Event]:
    """Fetch events from MIT and Harvard calendars."""
    all_events: list[Event] = []

    for name, fetcher in [("MIT", _fetch_mit), ("Harvard", _fetch_harvard)]:
        try:
            result = await fetcher(settings)
            all_events.extend(result)
        except Exception:
            logger.exception("%s event fetch failed", name)

    return all_events
