"""Boston-area calendar scrapers — The Boston Calendar, Do617, ArtsBoston."""

from __future__ import annotations

import logging
import re
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





# Regex for date strings like "Saturday, Mar 07, 2026 5:00a" or "Saturday, Mar 07"
_BC_DATE_RE = re.compile(
    r"(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),\s+"
    r"((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2}"
    r"(?:,\s+\d{4})?(?:\s+\d{1,2}:\d{2}[ap])?)"
)

# "goes until MM/DD" marks an ongoing/multi-day listing on The Boston Calendar
_BC_ONGOING_RE = re.compile(r"goes until\s+(\d{1,2})/(\d{1,2})")

_BC_DATE_FORMATS = (
    "%b %d, %Y %I:%M%p",   # "Mar 07, 2026 5:00AM" (after we normalize a→AM)
    "%b %d, %Y %H:%M",     # "Mar 07, 2026 17:00"
    "%b %d, %Y",            # "Mar 07, 2026"
    "%b %d",                # "Mar 07"  (we append current year before trying)
)


def _parse_bc_date(raw: str) -> datetime | None:
    """Parse a Boston Calendar date fragment extracted by *_BC_DATE_RE*."""
    s = raw.strip()
    # Normalize trailing single-char am/pm: "5:00a" → "5:00AM", "5:00p" → "5:00PM"
    s = re.sub(r"(\d:\d{2})a$", r"\1AM", s)
    s = re.sub(r"(\d:\d{2})p$", r"\1PM", s)
    year = datetime.now().year
    for fmt in _BC_DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            pass
    # If no year was in the string, append current year and retry
    if "," not in s or not re.search(r"\d{4}", s):
        s_with_year = f"{s}, {year}" if "," not in s else re.sub(r"$", f" {year}", s)
        for fmt in _BC_DATE_FORMATS:
            try:
                return datetime.strptime(s_with_year.strip(), fmt)
            except ValueError:
                pass
    return None


# ── The Boston Calendar ───────────────────────────────────────────────────────

async def _fetch_boston_calendar(client: httpx.AsyncClient) -> list[Event]:
    """Scrape thebostoncalendar.com/events for events."""
    url = "https://www.thebostoncalendar.com/events"
    headers = {"User-Agent": USER_AGENT}

    resp = await client.get(url, headers=headers)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "lxml")
    events: list[Event] = []

    now = datetime.now(timezone.utc)
    cutoff = now + timedelta(days=14)

    # Find every parent div that contains an h3 > a (event title link)
    seen_titles: set[str] = set()
    for h3_a in soup.select("h3 a[href]"):
        container = h3_a.find_parent("div")
        if container is None:
            continue

        title = h3_a.get_text(strip=True)
        if not title or len(title) < 3:
            continue
        if title in seen_titles:
            continue
        seen_titles.add(title)

        # URL
        href = h3_a["href"]
        event_url = href if href.startswith("http") else f"https://www.thebostoncalendar.com{href}"

        # Extract date from container text using regex
        container_text = container.get_text(" ", strip=True)
        date_match = _BC_DATE_RE.search(container_text)
        date_str = date_match.group(1) if date_match else ""
        start_time = _parse_bc_date(date_str) if date_str else parse_event_dt(date_str)

        # "goes until MM/DD" marks an ongoing/multi-day listing (beer gardens,
        # restaurants, "Now Open" venues, summer concert *series*). The Boston
        # Calendar stamps these with the *current* date, so without this they'd
        # masquerade as a concrete "today @ midnight" event every day we scrape.
        # Treat them as ongoing (start_time=None → email's undated/ongoing bucket).
        ongoing_match = _BC_ONGOING_RE.search(container_text)
        until_label = ""
        if ongoing_match:
            start_time = None
            mm, dd = int(ongoing_match.group(1)), int(ongoing_match.group(2))
            try:
                until_label = datetime(now.year, mm, dd).strftime("through %b %-d")
            except ValueError:
                until_label = ""

        # Skip events in the past (more than 1 day ago) or too far in the future
        if start_time:
            st_utc = start_time.replace(tzinfo=timezone.utc) if start_time.tzinfo is None else start_time
            if st_utc < now - timedelta(days=1) or st_utc > cutoff:
                continue

        # Location: text after the date match, before the next obvious boundary
        location = ""
        if date_match:
            after_date = container_text[date_match.end():].strip()
            # Take the first line-like chunk (up to next date pattern or end)
            loc_part = after_date.split("\n")[0].strip(" |·-–—")
            # Drop the "goes until MM/DD" fragment that bleeds into the location
            loc_part = _BC_ONGOING_RE.sub("", loc_part).strip(" |·-–—")
            if loc_part and len(loc_part) < 200:
                location = loc_part

        # Surface the run-end date for ongoing listings so it's not lost
        display_title = f"{title} ({until_label})" if until_label else title

        # Image
        img_tag = container.find("img", src=True)
        image_url = img_tag["src"] if img_tag else None

        # Price
        price_tag = container.find(class_=lambda c: c and "price" in str(c).lower())
        price = price_tag.get_text(strip=True) if price_tag else None

        events.append(
            Event(
                id=make_event_id("boston_calendar", title, date_str),
                source=EventSource.BOSTON_CALENDAR,
                title=display_title,
                description="",
                url=event_url,
                start_time=start_time,
                location_name=location,
                location_address="Boston, MA",
                price=price,
                image_url=image_url,
            )
        )

    logger.info("The Boston Calendar returned %d events", len(events))
    return events


# ── Do617 ─────────────────────────────────────────────────────────────────────

async def _fetch_do617(client: httpx.AsyncClient) -> list[Event]:
    """Scrape do617.com for events."""
    url = "https://do617.com/events"
    headers = {"User-Agent": USER_AGENT}

    resp = await client.get(url, headers=headers)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "lxml")
    events: list[Event] = []

    now = datetime.now(timezone.utc)
    cutoff = now + timedelta(days=14)

    cards = soup.select(
        "div.event-card, "
        "div[class*='ds-listing'], "
        "div[class*='ds-event'], "
        "article.event"
    )
    if not cards:
        cards = soup.select("div[class*='event'], a[class*='event']")

    for card in cards:
        link_tag = card.find("a", href=True) if card.name != "a" else card
        event_url = ""
        if link_tag:
            href = link_tag.get("href", "")
            event_url = href if href.startswith("http") else f"https://do617.com{href}"

        title_tag = (
            card.find(class_=lambda c: c and "title" in str(c).lower())
            or card.find(["h2", "h3", "h4"])
        )
        title = title_tag.get_text(strip=True) if title_tag else ""
        if not title or len(title) < 3:
            continue

        date_tag = card.find("time") or card.find(class_=lambda c: c and "date" in str(c).lower())
        date_str = ""
        if date_tag:
            date_str = date_tag.get("datetime", "") or date_tag.get_text(strip=True)
        start_time = parse_event_dt(date_str)

        if start_time and start_time > cutoff:
            continue

        location_tag = card.find(class_=lambda c: c and ("venue" in str(c).lower() or "location" in str(c).lower()))
        location = location_tag.get_text(strip=True) if location_tag else ""

        img_tag = card.find("img", src=True)
        image_url = img_tag["src"] if img_tag else None

        price_tag = card.find(class_=lambda c: c and "price" in str(c).lower())
        price = price_tag.get_text(strip=True) if price_tag else None

        events.append(
            Event(
                id=make_event_id("do617", title, date_str),
                source=EventSource.DO617,
                title=title,
                url=event_url,
                start_time=start_time,
                location_name=location,
                location_address="Boston, MA",
                price=price,
                image_url=image_url,
            )
        )

    logger.info("Do617 returned %d events", len(events))
    return events


# ── ArtsBoston ────────────────────────────────────────────────────────────────

async def _fetch_artsboston(client: httpx.AsyncClient) -> list[Event]:
    """Scrape ArtsBoston for arts events."""
    url = "https://www.artsboston.org/events"
    headers = {"User-Agent": USER_AGENT}

    resp = await client.get(url, headers=headers)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "lxml")
    events: list[Event] = []

    now = datetime.now(timezone.utc)
    cutoff = now + timedelta(days=14)

    cards = soup.select(
        "div.event-card, "
        "div[class*='event-listing'], "
        "div[class*='show-card'], "
        "article.event"
    )
    if not cards:
        cards = soup.select("div[class*='event'], li[class*='event']")

    for card in cards:
        link_tag = card.find("a", href=True)
        event_url = ""
        if link_tag:
            href = link_tag["href"]
            event_url = href if href.startswith("http") else f"https://calendar.artsboston.org{href}"

        title_tag = (
            card.find(class_=lambda c: c and "title" in str(c).lower())
            or card.find(["h2", "h3", "h4"])
        )
        title = title_tag.get_text(strip=True) if title_tag else ""
        if not title or len(title) < 3:
            continue

        date_tag = card.find("time") or card.find(class_=lambda c: c and "date" in str(c).lower())
        date_str = ""
        if date_tag:
            date_str = date_tag.get("datetime", "") or date_tag.get_text(strip=True)
        start_time = parse_event_dt(date_str)

        if start_time and start_time > cutoff:
            continue

        location_tag = card.find(class_=lambda c: c and ("venue" in str(c).lower() or "location" in str(c).lower()))
        location = location_tag.get_text(strip=True) if location_tag else ""

        desc_tag = card.find(class_=lambda c: c and "description" in str(c).lower()) or card.find("p")
        description = desc_tag.get_text(strip=True)[:500] if desc_tag else ""

        img_tag = card.find("img", src=True)
        image_url = img_tag["src"] if img_tag else None

        price_tag = card.find(class_=lambda c: c and "price" in str(c).lower())
        price = price_tag.get_text(strip=True) if price_tag else None

        events.append(
            Event(
                id=make_event_id("artsboston", title, date_str),
                source=EventSource.ARTSBOSTON,
                title=title,
                description=description,
                url=event_url,
                start_time=start_time,
                location_name=location,
                location_address="Boston, MA",
                category="arts",
                price=price,
                image_url=image_url,
            )
        )

    logger.info("ArtsBoston returned %d events", len(events))
    return events


# ── Public entry point ────────────────────────────────────────────────────────

async def fetch_boston_events(settings: Settings) -> list[Event]:
    """Fetch events from Boston-area community calendars."""
    all_events: list[Event] = []

    async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True) as client:
        sources = [
            ("The Boston Calendar", _fetch_boston_calendar),
            ("Do617", _fetch_do617),
            ("ArtsBoston", _fetch_artsboston),
        ]
        for name, fetcher in sources:
            try:
                result = await fetcher(client)
                all_events.extend(result)
            except Exception:
                logger.exception("%s scrape failed", name)

    return all_events
