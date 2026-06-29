"""Community event sources — libraries, theatres, performing arts."""

from __future__ import annotations

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


def _combine_dt(date: datetime, time_str: str) -> datetime | None:
    """Combine a date with a time like '5:30pm' / '11:00am' / '7pm'."""
    time_str = time_str.strip().lower().replace(".", "").replace(" ", "")
    for fmt in ("%I:%M%p", "%I%p"):
        try:
            t = datetime.strptime(time_str, fmt)
            return date.replace(hour=t.hour, minute=t.minute)
        except ValueError:
            continue
    return None


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
    """Scrape Coolidge Corner Theatre upcoming programs.

    Drupal listing with no JSON feed. Each program is a `.film-card`; the
    date is in a `.datepicker` span concatenated into the title (e.g.
    "Sun6/28–Thu7/2"), and the clean title is the link's title= attribute.
    Cards appear twice (outer + nested view) — dedupe by detail href.
    """
    url = "https://coolidge.org/films-events/upcoming-programs"
    now = datetime.now(timezone.utc)
    cutoff = now + timedelta(days=30)
    events: list[Event] = []
    seen: set[str] = set()

    async with httpx.AsyncClient(headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT, follow_redirects=True) as client:
        try:
            resp = await client.get(url)
            resp.raise_for_status()
        except Exception as exc:
            logger.warning("Coolidge fetch failed: %s", exc)
            return []

    soup = BeautifulSoup(resp.text, "html.parser")
    for card in soup.select(".film-card"):
        link = card.select_one("a.film-card__link[href]")
        if not link:
            continue
        href = link.get("href", "")
        if not href or href in seen:
            continue
        seen.add(href)

        title = (link.get("title") or "").strip()
        if not title:
            tnode = card.select_one(".film-card__title")
            title = tnode.get_text(strip=True) if tnode else ""
        if not title:
            continue

        dp = card.select_one(".film-card__title .datepicker") or card.select_one(".datepicker")
        m = re.search(r"(\d{1,2})/(\d{1,2})", dp.get_text(strip=True)) if dp else None
        if not m:
            continue
        month, day = int(m.group(1)), int(m.group(2))
        year = now.year
        try:
            start_time = datetime(year, month, day)
        except ValueError:
            continue
        # no year on page — roll forward if the date already passed (Dec→Jan)
        if start_time.replace(tzinfo=timezone.utc) < now - timedelta(days=2):
            start_time = start_time.replace(year=year + 1)
        if start_time.replace(tzinfo=timezone.utc) > cutoff:
            continue

        desc_el = card.select_one(".film-card__excerpt")
        description = desc_el.get_text(strip=True)[:300] if desc_el else ""

        src_el = card.select_one(".film-card__image picture source[srcset]") or card.select_one("img[src]")
        image_url = None
        if src_el:
            raw = src_el.get("srcset", "").split()[0] if src_el.get("srcset") else src_el.get("src", "")
            if raw:
                image_url = raw if raw.startswith("http") else f"https://coolidge.org{raw}"

        events.append(Event(
            id=make_event_id("coolidge", title, str(start_time.date())),
            source=EventSource.ARTSBOSTON,
            title=title,
            description=description,
            url=href if href.startswith("http") else f"https://coolidge.org{href}",
            start_time=start_time,
            location_name="Coolidge Corner Theatre",
            location_address="290 Harvard St, Brookline, MA 02446",
            organizer="Coolidge Corner Theatre",
            category="arts",
            image_url=image_url,
        ))

    logger.info("Coolidge returned %d events", len(events))
    return events


# ── Bowery Presents Boston ───────────────────────────────────────────────────

async def fetch_bowery_events(settings: Settings) -> list[Event]:
    """Fetch Bowery Presents Boston shows (Roadrunner, Sinclair, Royale, ...).

    The calendar page embeds an AXS/AEG widget that loads events from a static
    JSON file (its URL is in the page's `data-file` attribute). That file is the
    AEG national feed, so we filter to MA venues. Window: next 60 days (concerts
    are typically announced well ahead).
    """
    page_url = "https://www.bowerypresents.com/boston/calendar/"
    fallback = "https://aegwebprod.blob.core.windows.net/json/resources/8/events/7301mbln09/events.json"
    now = datetime.now(timezone.utc)
    cutoff = now + timedelta(days=60)
    events: list[Event] = []
    seen: set[str] = set()

    async with httpx.AsyncClient(headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT, follow_redirects=True) as client:
        # Resolve the feed URL(s) from the page (token can rotate), fall back to known.
        feed_urls: list[str] = []
        try:
            page = await client.get(page_url)
            feed_urls = list(dict.fromkeys(
                re.findall(r'data-file="([^"]+events\.json)"', page.text)
            ))
        except Exception as exc:
            logger.warning("Bowery page fetch failed: %s", exc)
        if not feed_urls:
            feed_urls = [fallback]

        raw_events: list[dict] = []
        for furl in feed_urls:
            try:
                fr = await client.get(furl)
                fr.raise_for_status()
                raw_events.extend(fr.json().get("events", []))
            except Exception as exc:
                logger.warning("Bowery feed %s failed: %s", furl, exc)

    for ev in raw_events:
        venue = ev.get("venue") or {}
        if venue.get("state") != "MA":
            continue
        title_obj = ev.get("title") or {}
        title = (title_obj.get("eventTitleText") or title_obj.get("headlinersText") or "").strip()
        if not title:
            continue
        start_time = parse_event_dt(ev.get("eventDateTimeISO") or "")
        if not start_time:
            continue
        start_aware = start_time if start_time.tzinfo else start_time.replace(tzinfo=timezone.utc)
        if start_aware < now or start_aware > cutoff:
            continue

        ticketing = ev.get("ticketing") or {}
        url = ticketing.get("url") or ticketing.get("ticketURL") or ""
        if url in seen:
            continue
        seen.add(url or title + (ev.get("eventDateTimeISO") or ""))

        media = ev.get("media")
        image_url = None
        if isinstance(media, dict):
            for key in ("17", "18", "1"):
                if isinstance(media.get(key), dict) and media[key].get("file_name"):
                    image_url = media[key]["file_name"]
                    break
        if not image_url and isinstance(venue.get("media"), dict):
            vm = venue["media"].get("14")
            if isinstance(vm, dict):
                image_url = vm.get("file_name")

        price = None
        low, high = ev.get("ticketPriceLow"), ev.get("ticketPriceHigh")
        if low and low != "$0":
            price = f"{low}–{high}" if high and high != low else low

        events.append(Event(
            id=make_event_id("bowery", title, ev.get("eventDateTimeISO") or ""),
            source=EventSource.ARTSBOSTON,
            title=title,
            description=BeautifulSoup(ev.get("description") or "", "html.parser").get_text(" ", strip=True)[:300],
            url=url,
            start_time=start_time,
            location_name=venue.get("title") or "Bowery Presents",
            location_address=venue.get("address_line") or "Boston, MA",
            organizer="Bowery Presents",
            category="music",
            image_url=image_url,
            price=price,
        ))

    logger.info("Bowery Presents returned %d events", len(events))
    return events


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
    """Fetch Boston Symphony Orchestra concerts via the site's Algolia index.

    bso.org is a client-rendered Craft site backed by Algolia (search-only key
    exposed in the page). We pull "Upcoming Events" and drop Tanglewood/TMC/TLI
    (the Berkshires summer venue, ~130mi away) to keep Boston/Symphony Hall shows.
    """
    now = datetime.now(timezone.utc)
    cutoff = now + timedelta(days=60)
    events: list[Event] = []
    away = ("tanglewood", "tmc", "tli", "berkshire")

    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            resp = await client.post(
                "https://T49PH09ZFX-dsn.algolia.net/1/indexes/bso_prd_env_site_search/query",
                headers={
                    "X-Algolia-Application-Id": "T49PH09ZFX",
                    "X-Algolia-API-Key": "1cc6d592d0f49c7571c372cc99e54a50",
                    "Content-Type": "application/json",
                },
                json={"query": "", "hitsPerPage": 300, "facetFilters": [["section:Upcoming Events"]]},
            )
            resp.raise_for_status()
            hits = resp.json().get("hits", [])
    except Exception as exc:
        logger.warning("BSO Algolia fetch failed: %s", exc)
        return []

    for hit in hits:
        title = re.sub(r"\s+", " ", (hit.get("title") or "")).strip()
        if not title:
            continue
        url_path = hit.get("url") or ""
        if any(w in url_path.lower() or w in title.lower() for w in away):
            continue
        start_time = parse_event_dt((hit.get("firstPerformanceDate") or {}).get("date") or "")
        if not start_time:
            continue
        start_aware = start_time if start_time.tzinfo else start_time.replace(tzinfo=timezone.utc)
        if start_aware < now or start_aware > cutoff:
            continue

        events.append(Event(
            id=make_event_id("bso", title, str(start_time.date())),
            source=EventSource.ARTSBOSTON,
            title=title,
            description=BeautifulSoup(hit.get("excerpt") or "", "html.parser").get_text(" ", strip=True)[:300],
            url=f"https://www.bso.org{url_path}" if url_path.startswith("/") else url_path,
            start_time=start_time,
            location_name="Symphony Hall",
            location_address="301 Massachusetts Ave, Boston, MA 02115",
            organizer="Boston Symphony Orchestra",
            category="music",
            image_url=hit.get("image"),
        ))

    logger.info("BSO returned %d events", len(events))
    return events


# ── City of Boston Events ───────────────────────────────────────────────────

async def fetch_boston_gov_events(settings: Settings) -> list[Event]:
    """Scrape the City of Boston events listing.

    Drupal 10, server-rendered, no JSON feed. Events are grouped under
    `h2.listing-group-title` date headers ("June 29, 2026"); each event card is
    an `article.calendar-listing-wrapper` with the title in `.teaser .title` and
    times in `.time-range`. We walk the DOM in order, carrying the current date.
    """
    url = "https://www.boston.gov/events"
    events: list[Event] = []

    async with httpx.AsyncClient(headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT, follow_redirects=True) as client:
        try:
            resp = await client.get(url)
            resp.raise_for_status()
        except Exception as exc:
            logger.warning("Boston.gov fetch failed: %s", exc)
            return []

    soup = BeautifulSoup(resp.text, "html.parser")
    current_date: datetime | None = None

    for el in soup.select("h2.listing-group-title, article.calendar-listing-wrapper"):
        classes = el.get("class") or []
        if "listing-group-title" in classes:
            try:
                current_date = datetime.strptime(el.get_text(strip=True), "%B %d, %Y")
            except ValueError:
                current_date = None
            continue

        if current_date is None:
            continue

        title_el = el.select_one(".teaser .title")
        title = title_el.get_text(strip=True) if title_el else ""
        if not title:
            continue

        # "5:30pm-6:30pm" → start/end times on the group date
        start_time, end_time = current_date, None
        tr_el = el.select_one(".time-range")
        if tr_el:
            parts = [p.strip() for p in tr_el.get_text(strip=True).split("-")]
            start_time = _combine_dt(current_date, parts[0]) or current_date
            if len(parts) > 1:
                end_time = _combine_dt(current_date, parts[1])

        node_id = (el.get("id") or "").replace("node-", "")
        event_url = f"https://www.boston.gov/node/{node_id}" if node_id else url

        addr_el = el.select_one('[itemprop="streetAddress"]')
        locality = el.select_one(".locality")
        location_name = addr_el.get_text(strip=True) if addr_el else "Boston"
        location_address = ", ".join(
            p.get_text(strip=True) for p in (addr_el, locality) if p
        ) or "Boston, MA"

        desc_el = el.select_one(".event-details .description")
        description = desc_el.get_text(" ", strip=True)[:300] if desc_el else ""

        events.append(Event(
            id=make_event_id("boston_gov", title, str(start_time.date())),
            source=EventSource.ARTSBOSTON,
            title=title,
            description=description,
            url=event_url,
            start_time=start_time,
            end_time=end_time,
            location_name=location_name,
            location_address=location_address,
            organizer="City of Boston",
            category="social",
        ))

    logger.info("Boston.gov returned %d events", len(events))
    return events

# NOTE: Improv Asylum retired 2026-06-29 — its WP `ia_shows` post type has no
# dated instances (only recurring formats), and the real schedule lives on Tixr
# behind DataDome bot protection (needs a headless browser). Not worth it.
