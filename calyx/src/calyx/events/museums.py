"""Museum and arts venue event scrapers — ICA, MFA, Gardner, MIT List, Harvard Art Museums."""

from __future__ import annotations

import asyncio
import json
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

def _parse_ica_date(text: str) -> datetime | None:
    """Parse ICA date like "Thu, Jul 2, 5–9 PM" (abbrev month, no year) → datetime."""
    m = re.search(r"([A-Z][a-z]{2})\s+(\d{1,2})", text)
    if not m:
        return None
    now = datetime.now(timezone.utc)
    try:
        dt = datetime.strptime(f"{m.group(1)} {m.group(2)} {now.year}", "%b %d %Y")
    except ValueError:
        return None
    if dt.replace(tzinfo=timezone.utc) < now - timedelta(days=2):
        dt = dt.replace(year=now.year + 1)
    tm = re.search(r"(\d{1,2})(?::(\d{2}))?\s*(AM|PM)", text)
    if tm:
        hour = int(tm.group(1)) % 12
        if tm.group(3).upper() == "PM":
            hour += 12
        dt = dt.replace(hour=hour, minute=int(tm.group(2) or 0))
    return dt


async def _fetch_ica(settings: Settings) -> list[Event]:
    """Scrape ICA Boston events (the calendar lists events in `.node-event` cards)."""
    url = "https://www.icaboston.org/events"
    now = datetime.now(timezone.utc)
    cutoff = now + timedelta(days=30)
    events: list[Event] = []
    seen: set[str] = set()

    async with httpx.AsyncClient(headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT, follow_redirects=True) as client:
        try:
            resp = await client.get(url)
            resp.raise_for_status()
        except Exception as exc:
            logger.warning("ICA fetch failed: %s", exc)
            return []

    soup = BeautifulSoup(resp.text, "html.parser")
    for card in soup.select(".node-event"):
        title_el = card.select_one("h3, .field-name-title")
        if not title_el:
            continue
        title = title_el.get_text(strip=True)
        if not title or len(title) < 3:
            continue

        date_el = card.select_one(".event-date-display, .field-name-event-date")
        start_time = _parse_ica_date(date_el.get_text(" ", strip=True)) if date_el else None
        if not start_time or start_time.replace(tzinfo=timezone.utc) > cutoff:
            continue

        link_el = card.select_one('a[href*="/events/"]')
        event_url = link_el.get("href", "") if link_el else ""
        if event_url and not event_url.startswith("http"):
            event_url = f"https://www.icaboston.org{event_url}"
        if event_url in seen:
            continue
        seen.add(event_url)

        img_el = card.select_one("img[src], img[data-src]")
        image_url = (img_el.get("src") or img_el.get("data-src")) if img_el else None

        events.append(Event(
            id=make_event_id("ica", title, str(start_time.date())),
            source=EventSource.ARTSBOSTON,
            title=title,
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

_MONTHS = "January|February|March|April|May|June|July|August|September|October|November|December"


def _parse_mfa_date(text: str) -> datetime | None:
    """Parse "Monday, June 29–Thursday, July 2, 2026 9:00 am–4:00 pm" → start datetime.

    The year only appears once (after the last date); the start date has none.
    """
    year_m = re.search(r"\b(20\d{2})\b", text)
    date_m = re.search(rf"({_MONTHS})\s+(\d{{1,2}})", text)
    if not year_m or not date_m:
        return None
    try:
        dt = datetime.strptime(f"{date_m.group(1)} {date_m.group(2)}, {year_m.group(1)}", "%B %d, %Y")
    except ValueError:
        return None
    time_m = re.search(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)", text, re.IGNORECASE)
    if time_m:
        hour = int(time_m.group(1)) % 12
        if time_m.group(3).lower() == "pm":
            hour += 12
        dt = dt.replace(hour=hour, minute=int(time_m.group(2) or 0))
    return dt


async def _fetch_mfa(settings: Settings) -> list[Event]:
    """Scrape MFA Boston programs (a server-rendered Drupal view; paged ?page=N)."""
    now = datetime.now(timezone.utc)
    cutoff = now + timedelta(days=30)
    events: list[Event] = []
    seen: set[str] = set()
    seen_titles: set[str] = set()  # collapse repeating multi-session studio classes

    async with httpx.AsyncClient(headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT, follow_redirects=True) as client:
        for page in range(6):  # page 1 already reaches ~August; 6 covers a month
            try:
                resp = await client.get("https://www.mfa.org/programs", params={"page": page})
                resp.raise_for_status()
            except Exception as exc:
                logger.warning("MFA page %d fetch failed: %s", page, exc)
                break

            wells = BeautifulSoup(resp.text, "html.parser").select(".well")
            if not wells:
                break

            page_past_cutoff = True
            for well in wells:
                title_el = well.select_one("h2.field-content a, h2 a")
                if not title_el:
                    continue
                title = title_el.get_text(strip=True)
                href = title_el.get("href", "")
                if not title or not href:
                    continue

                info_el = well.select_one(".date-display-range, .date-display-single, p.field-content.info, .info")
                start_time = _parse_mfa_date(info_el.get_text(" ", strip=True)) if info_el else None
                if not start_time:
                    continue
                if start_time.replace(tzinfo=timezone.utc) < now - timedelta(days=1):
                    continue
                if start_time.replace(tzinfo=timezone.utc) > cutoff:
                    continue
                page_past_cutoff = False

                event_url = href if href.startswith("http") else f"https://www.mfa.org{href}"
                if event_url in seen or title.lower() in seen_titles:
                    continue
                seen.add(event_url)
                seen_titles.add(title.lower())

                img_el = well.select_one("picture img, img")
                image_url = img_el.get("src") if img_el and img_el.get("src") else None

                events.append(Event(
                    id=make_event_id("mfa", title, str(start_time.date())),
                    source=EventSource.ARTSBOSTON,
                    title=title,
                    url=event_url,
                    start_time=start_time,
                    location_name="Museum of Fine Arts Boston",
                    location_address="465 Huntington Ave, Boston, MA 02115",
                    organizer="Museum of Fine Arts",
                    image_url=image_url,
                    category="arts",
                ))

            if page_past_cutoff and page > 0:
                break

    logger.info("MFA Boston returned %d events", len(events))
    return events


# ── MIT List Visual Arts Center ───────────────────────────────────────────────

async def _fetch_mit_list(settings: Settings) -> list[Event]:
    """Scrape MIT List Visual Arts Center events."""
    url = "https://listart.mit.edu/calendar"
    events: list[Event] = []

    async with httpx.AsyncClient(headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT, follow_redirects=True) as client:
        try:
            resp = await client.get(url)
            resp.raise_for_status()
        except Exception as exc:
            logger.warning("MIT List fetch failed: %s", exc)
            return []

    soup = BeautifulSoup(resp.text, "html.parser")
    for card in soup.select(".card--event"):
        title_el = card.select_one("h2, h3, .card__title, [class*='title']")
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

        # MIT List exposes a machine-readable ISO datetime on the card's <time> element
        date_el = card.select_one("time")
        start_time = None
        if date_el:
            start_time = parse_event_dt(date_el.get("datetime") or date_el.get_text(strip=True))

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
    for card in soup.select(".isg-events-list__item-wrapper"):
        title_el = card.select_one(".isg-card__title")
        if not title_el:
            continue
        title = title_el.get_text(strip=True)
        if not title or len(title) < 3:
            continue

        # Date text like "Sunday July 5, 2026 10 am - 5 pm" — extract "July 5, 2026"
        date_el = card.select_one(".isg-events-list__date")
        start_time = None
        if date_el:
            m = re.search(r"([A-Z][a-z]+ \d{1,2}, \d{4})", date_el.get_text(" ", strip=True))
            if m:
                try:
                    start_time = datetime.strptime(m.group(1), "%B %d, %Y")
                except ValueError:
                    start_time = None
        if not start_time:
            continue

        # Canonical event URL = the "More info" (secondary) link, not the ticketing link
        link_el = card.select_one("a.isg-btn--secondary") or card.select_one("a[href]")
        event_url = ""
        if link_el:
            href = link_el.get("href", "")
            event_url = href if href.startswith("http") else f"https://www.gardnermuseum.org{href}"

        cat_el = card.select_one(".isg-eyebrow__text")
        category = cat_el.get_text(strip=True) if cat_el else "arts"
        # the eyebrow sometimes has the date string concatenated on the end
        category = re.sub(r"[A-Z][a-z]+ \d{1,2}, \d{4}.*$", "", category).strip() or "arts"

        img_el = card.select_one(".isg-media picture img") or card.select_one("img")
        image_url = None
        if img_el and img_el.get("src"):
            src = img_el["src"]
            image_url = src if src.startswith("http") else f"https://www.gardnermuseum.org{src}"

        events.append(Event(
            id=make_event_id("gardner", title, str(start_time.date())),
            source=EventSource.ARTSBOSTON,
            title=title,
            url=event_url,
            start_time=start_time,
            location_name="Isabella Stewart Gardner Museum",
            location_address="25 Evans Way, Boston, MA 02115",
            organizer="Gardner Museum",
            image_url=image_url,
            category=category,
        ))

    logger.info("Gardner Museum returned %d events", len(events))
    return events


# ── Harvard Art Museums ──────────────────────────────────────────────────────

_HARVARD_ART_CATEGORIES = {
    1: "Trip", 2: "Performance", 3: "Gallery Talk", 4: "Lecture",
    5: "Supporter Event", 6: "Other", 7: "Special Event", 8: "Student Event",
    9: "Symposium", 10: "Seminar", 11: "Workshop", 12: "Tour", 13: "Film",
}


async def _fetch_harvard_art(settings: Settings) -> list[Event]:
    """Fetch Harvard Art Museums events.

    The calendar is an Alpine.js SPA, but the full event list is server-rendered
    into the page as a `var initialEvents = [].concat([...]);` JS array — parse
    that JSON rather than scraping (non-existent) card markup.
    """
    url = "https://harvardartmuseums.org/calendar"
    now = datetime.now(timezone.utc)
    cutoff = now + timedelta(days=30)
    events: list[Event] = []

    async with httpx.AsyncClient(headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT, follow_redirects=True) as client:
        try:
            resp = await client.get(url)
            resp.raise_for_status()
        except Exception as exc:
            logger.warning("Harvard Art Museums fetch failed: %s", exc)
            return []

    m = re.search(r"var initialEvents\s*=\s*\[\]\.concat\((\[.*?\])\)\s*;", resp.text, re.DOTALL)
    if not m:
        logger.warning("Harvard Art Museums: initialEvents array not found")
        return []
    try:
        items = json.loads(m.group(1))
    except json.JSONDecodeError:
        logger.warning("Harvard Art Museums: failed to parse initialEvents JSON")
        return []

    def _iso_z(raw: str | None) -> datetime | None:
        # dates look like "2026-07-01T16:30:00.000000Z" (UTC, microseconds)
        if not raw:
            return None
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return parse_event_dt(raw)

    for item in items:
        title = (item.get("title") or "").strip()
        if not title or "closed" in title.lower():
            continue
        start_time = _iso_z(item.get("date"))
        if not start_time:
            continue
        start_aware = start_time if start_time.tzinfo else start_time.replace(tzinfo=timezone.utc)
        if start_aware < now or start_aware > cutoff:
            continue

        image = item.get("image")
        image_url = None
        if isinstance(image, dict):
            image_url = (image.get("list") or image.get("hero") or image.get("original") or {}).get("url")

        addr_parts = [item.get("address"), item.get("city"), item.get("state")]
        location_address = ", ".join(p for p in addr_parts if p) or "32 Quincy St, Cambridge, MA 02138"

        events.append(Event(
            id=make_event_id("harvard_art", title, item.get("date") or ""),
            source=EventSource.ARTSBOSTON,
            title=title,
            description=(item.get("summary") or "")[:300],
            url=item.get("event_link") or f"https://harvardartmuseums.org/calendar/{item.get('slug', '')}",
            start_time=start_time,
            end_time=_iso_z(item.get("end_date")),
            location_name=item.get("institution") or "Harvard Art Museums",
            location_address=location_address,
            organizer="Harvard Art Museums",
            category=_HARVARD_ART_CATEGORIES.get(item.get("type"), "arts"),
            image_url=image_url,
        ))

    logger.info("Harvard Art Museums returned %d events", len(events))
    return events


# ── Museum of Science ────────────────────────────────────────────────────────

def _parse_mos_date(text: str) -> datetime | None:
    """Parse MoS free-text dates like "Friday, July 10 | Doors at 7:00p.m." → datetime.

    No year is given, so infer the current year and roll forward if already past.
    """
    m = re.search(rf"({_MONTHS})\s+(\d{{1,2}})", text)
    if not m:
        return None
    now = datetime.now(timezone.utc)
    try:
        dt = datetime.strptime(f"{m.group(1)} {m.group(2)}, {now.year}", "%B %d, %Y")
    except ValueError:
        return None
    if dt.replace(tzinfo=timezone.utc) < now - timedelta(days=2):
        dt = dt.replace(year=now.year + 1)
    tm = re.search(r"(\d{1,2})(?::(\d{2}))?\s*([ap])\.?m", text, re.IGNORECASE)
    if tm:
        hour = int(tm.group(1)) % 12
        if tm.group(3).lower() == "p":
            hour += 12
        dt = dt.replace(hour=hour, minute=int(tm.group(2) or 0))
    return dt


async def _fetch_mos(settings: Settings) -> list[Event]:
    """Fetch Museum of Science events from its Drupal event-listing JSON API.

    The /events page is an Angular app; this endpoint returns a JSON envelope
    whose `results[].event_listing` values are pre-rendered HTML fragments.
    """
    url = (
        "https://www.mos.org/api/v1/event-listing/event_detail/96+101/all/all/all/all"
        "?items_per_page=100&sort_by=date_asc"
    )
    now = datetime.now(timezone.utc)
    cutoff = now + timedelta(days=30)
    events: list[Event] = []

    async with httpx.AsyncClient(headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT, follow_redirects=True) as client:
        try:
            resp = await client.get(url)
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            logger.warning("Museum of Science fetch failed: %s", exc)
            return []

    for row in data.get("results", []):
        frag = row.get("event_listing") if isinstance(row, dict) else None
        if not frag:
            continue
        card = BeautifulSoup(frag, "html.parser")
        title_el = card.select_one("h3.listing-item__title a, h3 a")
        if not title_el:
            continue
        title = title_el.get_text(strip=True)
        href = title_el.get("href", "")
        if not title:
            continue

        date_el = card.select_one(".field--name-field-date-time-info, [class*='date-time']")
        start_time = _parse_mos_date(date_el.get_text(" ", strip=True)) if date_el else None
        if not start_time:
            continue
        if start_time.replace(tzinfo=timezone.utc) > cutoff:
            continue

        desc_el = card.select_one(".listing-item__summary")
        description = desc_el.get_text(" ", strip=True)[:300] if desc_el else ""
        cat_el = card.select_one(".listing-item__content-type, .taxonomy-tag")
        category = cat_el.get_text(strip=True) if cat_el else "learning"
        img_el = card.select_one("img[src]")
        image_url = None
        if img_el:
            src = img_el["src"]
            image_url = src if src.startswith("http") else f"https://www.mos.org{src}"

        events.append(Event(
            id=make_event_id("mos", title, str(start_time.date())),
            source=EventSource.ARTSBOSTON,
            title=title,
            description=description,
            url=href if href.startswith("http") else f"https://www.mos.org{href}",
            start_time=start_time,
            location_name="Museum of Science",
            location_address="1 Science Park, Boston, MA 02114",
            organizer="Museum of Science Boston",
            image_url=image_url,
            category=category,
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
