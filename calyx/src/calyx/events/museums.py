"""Museum and arts venue event scrapers — ICA, MFA, Gardner, MIT List, Harvard Art Museums."""

from __future__ import annotations

import asyncio
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




# ── ICA Boston ────────────────────────────────────────────────────────────────

async def _fetch_ica(settings: Settings) -> list[Event]:
    """Scrape ICA Boston events page."""
    url = "https://www.icaboston.org/events"
    events: list[Event] = []

    async with httpx.AsyncClient(headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT, follow_redirects=True) as client:
        try:
            resp = await client.get(url)
            resp.raise_for_status()
        except Exception as exc:
            logger.warning("ICA fetch failed: %s", exc)
            return []

    soup = BeautifulSoup(resp.text, "html.parser")
    # ICA uses article cards with class names
    for card in soup.select("article, .event-card, .views-row, [class*='event']")[:30]:
        title_el = card.select_one("h2, h3, .title, [class*='title']")
        if not title_el:
            continue
        title = title_el.get_text(strip=True)
        if not title or len(title) < 3:
            continue

        link_el = card.select_one("a[href]")
        event_url = ""
        if link_el:
            href = link_el.get("href", "")
            event_url = href if href.startswith("http") else f"https://www.icaboston.org{href}"

        date_el = card.select_one("time, .date, [class*='date']")
        start_time = None
        if date_el:
            dt_attr = date_el.get("datetime") or date_el.get_text(strip=True)
            start_time = parse_event_dt(dt_attr)

        desc_el = card.select_one("p, .description, .summary, [class*='desc']")
        description = desc_el.get_text(strip=True)[:300] if desc_el else ""

        img_el = card.select_one("img")
        image_url = img_el.get("src") if img_el else None

        date_str = str(start_time.date()) if start_time else ""
        events.append(Event(
            id=make_event_id("ica", title, date_str),
            source=EventSource.ARTSBOSTON,
            title=title,
            description=description,
            url=event_url,
            start_time=start_time,
            location_name="ICA Boston",
            location_address="25 Harbor Shore Dr, Boston, MA 02210",
            organizer="Institute of Contemporary Art",
            image_url=image_url,
            category="arts",
        ))

    logger.info("ICA Boston returned %d events", len(events))
    return events


# ── MFA Boston ────────────────────────────────────────────────────────────────

async def _fetch_mfa(settings: Settings) -> list[Event]:
    """Scrape MFA Boston programs/events."""
    url = "https://www.mfa.org/programs"
    events: list[Event] = []

    async with httpx.AsyncClient(headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT, follow_redirects=True) as client:
        try:
            resp = await client.get(url)
            resp.raise_for_status()
        except Exception as exc:
            logger.warning("MFA fetch failed: %s", exc)
            return []

    soup = BeautifulSoup(resp.text, "html.parser")
    for card in soup.select(".program-card, .event-item, article, [class*='program'], [class*='event']")[:30]:
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
            event_url = href if href.startswith("http") else f"https://www.mfa.org{href}"

        date_el = card.select_one("time, .date, [class*='date']")
        start_time = None
        if date_el:
            dt_attr = date_el.get("datetime") or date_el.get_text(strip=True)
            start_time = parse_event_dt(dt_attr)

        desc_el = card.select_one("p, .description, [class*='desc']")
        description = desc_el.get_text(strip=True)[:300] if desc_el else ""

        img_el = card.select_one("img")
        image_url = img_el.get("src") if img_el else None

        date_str = str(start_time.date()) if start_time else ""
        events.append(Event(
            id=make_event_id("mfa", title, date_str),
            source=EventSource.ARTSBOSTON,
            title=title,
            description=description,
            url=event_url,
            start_time=start_time,
            location_name="Museum of Fine Arts Boston",
            location_address="465 Huntington Ave, Boston, MA 02115",
            organizer="Museum of Fine Arts",
            image_url=image_url,
            category="arts",
        ))

    logger.info("MFA Boston returned %d events", len(events))
    return events


# ── MIT List Visual Arts Center ───────────────────────────────────────────────

async def _fetch_mit_list(settings: Settings) -> list[Event]:
    """Scrape MIT List Visual Arts Center events."""
    url = "https://listart.mit.edu/events-programs"
    events: list[Event] = []

    async with httpx.AsyncClient(headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT, follow_redirects=True) as client:
        try:
            resp = await client.get(url)
            resp.raise_for_status()
        except Exception as exc:
            logger.warning("MIT List fetch failed: %s", exc)
            return []

    soup = BeautifulSoup(resp.text, "html.parser")
    for card in soup.select(".views-row, article, .event, [class*='event']")[:20]:
        title_el = card.select_one("h2, h3, .title, [class*='title']")
        if not title_el:
            continue
        title = title_el.get_text(strip=True)
        if not title or len(title) < 3:
            continue

        link_el = title_el.select_one("a") or card.select_one("a[href]")
        event_url = ""
        if link_el:
            href = link_el.get("href", "")
            event_url = href if href.startswith("http") else f"https://listart.mit.edu{href}"

        date_el = card.select_one("time, .date, [class*='date']")
        start_time = None
        if date_el:
            dt_attr = date_el.get("datetime") or date_el.get_text(strip=True)
            start_time = parse_event_dt(dt_attr)

        desc_el = card.select_one("p, .description, .field-body")
        description = desc_el.get_text(strip=True)[:300] if desc_el else ""

        date_str = str(start_time.date()) if start_time else ""
        events.append(Event(
            id=make_event_id("list", title, date_str),
            source=EventSource.MIT,
            title=title,
            description=description,
            url=event_url,
            start_time=start_time,
            location_name="MIT List Visual Arts Center",
            location_address="20 Ames St, Cambridge, MA 02139",
            organizer="MIT List Visual Arts Center",
            category="arts",
        ))

    logger.info("MIT List Arts returned %d events", len(events))
    return events


# ── Isabella Stewart Gardner Museum ──────────────────────────────────────────

async def _fetch_gardner(settings: Settings) -> list[Event]:
    """Scrape Gardner Museum events."""
    url = "https://www.gardnermuseum.org/calendar"
    events: list[Event] = []

    async with httpx.AsyncClient(headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT, follow_redirects=True) as client:
        try:
            resp = await client.get(url)
            resp.raise_for_status()
        except Exception as exc:
            logger.warning("Gardner Museum fetch failed: %s", exc)
            return []

    soup = BeautifulSoup(resp.text, "html.parser")
    for card in soup.select("article, .event-card, .views-row, [class*='event'], .card")[:30]:
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
            event_url = href if href.startswith("http") else f"https://www.gardnermuseum.org{href}"

        date_el = card.select_one("time, .date, [class*='date']")
        start_time = None
        if date_el:
            dt_attr = date_el.get("datetime") or date_el.get_text(strip=True)
            start_time = parse_event_dt(dt_attr)

        desc_el = card.select_one("p, .description, [class*='desc']")
        description = desc_el.get_text(strip=True)[:300] if desc_el else ""

        img_el = card.select_one("img")
        image_url = img_el.get("src") if img_el else None

        date_str = str(start_time.date()) if start_time else ""
        events.append(Event(
            id=make_event_id("gardner", title, date_str),
            source=EventSource.ARTSBOSTON,
            title=title,
            description=description,
            url=event_url,
            start_time=start_time,
            location_name="Isabella Stewart Gardner Museum",
            location_address="25 Evans Way, Boston, MA 02115",
            organizer="Gardner Museum",
            image_url=image_url,
            category="arts",
        ))

    logger.info("Gardner Museum returned %d events", len(events))
    return events


# ── Harvard Art Museums ──────────────────────────────────────────────────────

async def _fetch_harvard_art(settings: Settings) -> list[Event]:
    """Scrape Harvard Art Museums events."""
    url = "https://harvardartmuseums.org/calendar"
    events: list[Event] = []

    async with httpx.AsyncClient(headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT, follow_redirects=True) as client:
        try:
            resp = await client.get(url)
            resp.raise_for_status()
        except Exception as exc:
            logger.warning("Harvard Art Museums fetch failed: %s", exc)
            return []

    soup = BeautifulSoup(resp.text, "html.parser")
    for card in soup.select("article, .event-card, .listing-item, [class*='event'], .card")[:30]:
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
            event_url = href if href.startswith("http") else f"https://harvardartmuseums.org{href}"

        date_el = card.select_one("time, .date, [class*='date']")
        start_time = None
        if date_el:
            dt_attr = date_el.get("datetime") or date_el.get_text(strip=True)
            start_time = parse_event_dt(dt_attr)

        desc_el = card.select_one("p, .description, [class*='desc']")
        description = desc_el.get_text(strip=True)[:300] if desc_el else ""

        date_str = str(start_time.date()) if start_time else ""
        events.append(Event(
            id=make_event_id("harvard_art", title, date_str),
            source=EventSource.ARTSBOSTON,
            title=title,
            description=description,
            url=event_url,
            start_time=start_time,
            location_name="Harvard Art Museums",
            location_address="32 Quincy St, Cambridge, MA 02138",
            organizer="Harvard Art Museums",
            category="arts",
        ))

    logger.info("Harvard Art Museums returned %d events", len(events))
    return events


# ── Museum of Science ────────────────────────────────────────────────────────

async def _fetch_mos(settings: Settings) -> list[Event]:
    """Scrape Museum of Science Boston events."""
    url = "https://www.mos.org/events"
    events: list[Event] = []

    async with httpx.AsyncClient(headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT, follow_redirects=True) as client:
        try:
            resp = await client.get(url)
            resp.raise_for_status()
        except Exception as exc:
            logger.warning("Museum of Science fetch failed: %s", exc)
            return []

    soup = BeautifulSoup(resp.text, "html.parser")
    for card in soup.select("article, .event-card, .views-row, [class*='event'], .card, .listing-item")[:30]:
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
            event_url = href if href.startswith("http") else f"https://www.mos.org{href}"

        date_el = card.select_one("time, .date, [class*='date']")
        start_time = None
        if date_el:
            dt_attr = date_el.get("datetime") or date_el.get_text(strip=True)
            start_time = parse_event_dt(dt_attr)

        desc_el = card.select_one("p, .description, [class*='desc']")
        description = desc_el.get_text(strip=True)[:300] if desc_el else ""

        img_el = card.select_one("img")
        image_url = img_el.get("src") if img_el else None

        date_str = str(start_time.date()) if start_time else ""
        events.append(Event(
            id=make_event_id("mos", title, date_str),
            source=EventSource.ARTSBOSTON,
            title=title,
            description=description,
            url=event_url,
            start_time=start_time,
            location_name="Museum of Science",
            location_address="1 Science Park, Boston, MA 02114",
            organizer="Museum of Science Boston",
            image_url=image_url,
            category="learning",
        ))

    logger.info("Museum of Science returned %d events", len(events))
    return events


# ── Boston Athenaeum ─────────────────────────────────────────────────────────

async def _fetch_athenaeum(settings: Settings) -> list[Event]:
    """Scrape Boston Athenaeum events."""
    url = "https://www.bostonathenaeum.org/events/"
    events: list[Event] = []

    async with httpx.AsyncClient(headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT, follow_redirects=True) as client:
        try:
            resp = await client.get(url)
            resp.raise_for_status()
        except Exception as exc:
            logger.warning("Boston Athenaeum fetch failed: %s", exc)
            return []

    soup = BeautifulSoup(resp.text, "html.parser")
    for card in soup.select("article, .event-card, .views-row, [class*='event'], .card, .tribe-events-calendar-list__event")[:30]:
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
            event_url = href if href.startswith("http") else f"https://www.bostonathenaeum.org{href}"

        date_el = card.select_one("time, .date, [class*='date']")
        start_time = None
        if date_el:
            dt_attr = date_el.get("datetime") or date_el.get_text(strip=True)
            start_time = parse_event_dt(dt_attr)

        desc_el = card.select_one("p, .description, [class*='desc']")
        description = desc_el.get_text(strip=True)[:300] if desc_el else ""

        date_str = str(start_time.date()) if start_time else ""
        events.append(Event(
            id=make_event_id("athenaeum", title, date_str),
            source=EventSource.ARTSBOSTON,
            title=title,
            description=description,
            url=event_url,
            start_time=start_time,
            location_name="Boston Athenaeum",
            location_address="10½ Beacon St, Boston, MA 02108",
            organizer="Boston Athenaeum",
            category="arts",
        ))

    logger.info("Boston Athenaeum returned %d events", len(events))
    return events


# ── Public entry point ────────────────────────────────────────────────────────

async def fetch_museum_events(settings: Settings) -> list[Event]:
    """Fetch events from Boston-area museums and arts venues."""
    tasks = [
        _fetch_ica(settings),
        _fetch_mfa(settings),
        _fetch_mit_list(settings),
        _fetch_gardner(settings),
        _fetch_harvard_art(settings),
        _fetch_mos(settings),
        _fetch_athenaeum(settings),
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    all_events: list[Event] = []
    names = ["ICA", "MFA", "MIT List", "Gardner", "Harvard Art", "Museum of Science", "Athenaeum"]
    for name, result in zip(names, results):
        if isinstance(result, Exception):
            logger.warning("%s museum fetch failed: %s", name, result)
        else:
            all_events.extend(result)
    return all_events
