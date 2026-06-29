"""Community event sources — libraries, theatres, performing arts."""

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
    """Fetch Boston Public Library events from the BiblioCommons JSON API.

    bpl.org/events is now a JS-rendered BiblioCommons SPA; the public gateway
    API returns clean structured events (title, start/end, location, description).
    """
    now = datetime.now(timezone.utc)
    cutoff = now + timedelta(days=30)
    url = "https://gateway.bibliocommons.com/v2/libraries/bpl/events"
    params = {"limit": 100, "startDate": now.strftime("%Y-%m-%d")}

    async with httpx.AsyncClient(headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT, follow_redirects=True) as client:
        try:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            logger.warning("BPL BiblioCommons fetch failed: %s", exc)
            return []

    entities = data.get("entities", {})
    locations = entities.get("locations", {})
    events: list[Event] = []

    for ev_id, ev in entities.get("events", {}).items():
        defn = ev.get("definition", {})
        if defn.get("isCancelled"):
            continue
        title = (defn.get("title") or "").strip()
        if not title:
            continue
        start_time = parse_event_dt(defn.get("start") or "")
        if not start_time:
            continue
        start_aware = start_time if start_time.tzinfo else start_time.replace(tzinfo=timezone.utc)
        if start_aware < now or start_aware > cutoff:
            continue

        loc = locations.get(str(defn.get("branchLocationId") or ""), {})
        venue = loc.get("name") or defn.get("nonBranchLocationId") or "Boston Public Library"

        description = defn.get("description") or ""
        if "<" in description:
            description = BeautifulSoup(description, "html.parser").get_text(" ", strip=True)

        events.append(Event(
            id=make_event_id("bpl", title, defn.get("start") or ""),
            source=EventSource.ARTSBOSTON,
            title=title,
            description=description[:500],
            url=f"https://bpl.bibliocommons.com/events/{ev_id}",
            start_time=start_time,
            end_time=parse_event_dt(defn.get("end") or ""),
            location_name=venue,
            location_address="700 Boylston St, Boston, MA 02116",
            organizer="Boston Public Library",
            category="learning",
        ))

    logger.info("BPL BiblioCommons returned %d events", len(events))
    return events


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
        url="https://coolidge.org/films-events/upcoming-programs",
        source_id="coolidge",
        venue_name="Coolidge Corner Theatre",
        venue_address="290 Harvard St, Brookline, MA 02446",
        organizer="Coolidge Corner Theatre",
        category="arts",
        base_url="https://coolidge.org",
    )


# ── Bowery Presents Boston ───────────────────────────────────────────────────

async def fetch_bowery_events(settings: Settings) -> list[Event]:
    """Scrape Bowery Presents Boston — covers Roadrunner, Sinclair, Royale."""
    return await _scrape_events_page(
        url="https://www.bowerypresents.com/boston/calendar/",
        source_id="bowery",
        venue_name="Bowery Presents Boston",
        venue_address="Boston, MA",
        organizer="Bowery Presents",
        category="music",
        base_url="https://www.bowerypresents.com",
    )


# ── Crossroads Presents ─────────────────────────────────────────────────────

async def fetch_crossroads_events(settings: Settings) -> list[Event]:
    """Scrape Crossroads Presents — covers Paradise Rock Club, Brighton Music Hall, MGM."""
    return await _scrape_events_page(
        url="https://crossroadspresents.com/pages/events",
        source_id="crossroads",
        venue_name="Crossroads Presents",
        venue_address="Boston, MA",
        organizer="Crossroads Presents",
        category="music",
        base_url="https://crossroadspresents.com",
    )


# ── ArtsEmerson ─────────────────────────────────────────────────────────────

async def fetch_artsemerson_events(settings: Settings) -> list[Event]:
    """Scrape ArtsEmerson — international performances at Emerson College venues."""
    return await _scrape_events_page(
        url="https://artsemerson.org/calendar/",
        source_id="artsemerson",
        venue_name="ArtsEmerson",
        venue_address="Boston, MA 02116",
        organizer="ArtsEmerson",
        category="arts",
        base_url="https://artsemerson.org",
    )


# ── BSO (Boston Symphony Orchestra) ─────────────────────────────────────────

async def fetch_bso_events(settings: Settings) -> list[Event]:
    """Scrape BSO events at Symphony Hall."""
    return await _scrape_events_page(
        url="https://www.bso.org/events",
        source_id="bso",
        venue_name="Symphony Hall",
        venue_address="301 Massachusetts Ave, Boston, MA 02115",
        organizer="Boston Symphony Orchestra",
        category="music",
        base_url="https://www.bso.org",
    )


# ── City of Boston Events ───────────────────────────────────────────────────

async def fetch_boston_gov_events(settings: Settings) -> list[Event]:
    """Scrape City of Boston official events page."""
    return await _scrape_events_page(
        url="https://www.boston.gov/events",
        source_id="boston_gov",
        venue_name="Boston",
        venue_address="Boston, MA",
        organizer="City of Boston",
        category="social",
        base_url="https://www.boston.gov",
    )


# ── Improv Asylum ───────────────────────────────────────────────────────────

async def fetch_improv_asylum_events(settings: Settings) -> list[Event]:
    """Scrape Improv Asylum comedy shows."""
    return await _scrape_events_page(
        url="https://improvasylum.com/events/",
        source_id="improv_asylum",
        venue_name="Improv Asylum",
        venue_address="216 Hanover St, Boston, MA 02113",
        organizer="Improv Asylum",
        category="comedy",
        base_url="https://improvasylum.com",
    )
