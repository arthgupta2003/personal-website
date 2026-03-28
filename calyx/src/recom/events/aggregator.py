"""Event aggregator — runs all sources in parallel, deduplicates results."""

from __future__ import annotations

import asyncio
import difflib
import hashlib
import logging
import re
import time
import unicodedata
from datetime import datetime

import anthropic

from recom.config import Settings
from recom.events.bandsintown import fetch_bandsintown
from recom.events.geocoder import geocode_events
from recom.events.resident_advisor import fetch_resident_advisor
from recom.events.boston_calendar import fetch_boston_events
from recom.events.dice import fetch_dice
from recom.events.eventbrite import fetch_eventbrite
from recom.events.luma import fetch_luma
from recom.events.meetup import fetch_meetup
from recom.events.museums import _fetch_ica, _fetch_mfa, _fetch_mit_list, _fetch_gardner, _fetch_harvard_art, _fetch_mos
from recom.events.community import (
    fetch_bpl_events, fetch_coolidge_events,
    fetch_bowery_events, fetch_bso_events,
    fetch_boston_gov_events, fetch_improv_asylum_events,
)
from recom.events.newsletters import extract_newsletter_events
from recom.events.outdoor import fetch_outdoor_events
from recom.events.songkick import fetch_songkick
from recom.events.ticketmaster import fetch_ticketmaster
from recom.events.timeout_boston import fetch_timeout_boston
from recom.events.university import _fetch_mit, _fetch_harvard, _fetch_localist
from recom.models import CostRecord, Event, EventSource, SourceStat

logger = logging.getLogger(__name__)


# ── Deduplication helpers ─────────────────────────────────────────────────────

def _normalize_title(title: str) -> str:
    """Lowercase, strip accents, remove punctuation/extra whitespace."""
    text = title.lower().strip()
    # Remove accents
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    # Remove punctuation
    text = re.sub(r"[^\w\s]", "", text)
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _dedup_key(event: Event) -> str:
    """Generate a dedup key from normalized title + date (day only)."""
    norm_title = _normalize_title(event.title)
    day = ""
    if event.start_time:
        day = event.start_time.strftime("%Y-%m-%d")
    raw = f"{norm_title}|{day}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _data_score(ev: Event) -> int:
    return sum([
        bool(ev.description),
        bool(ev.url),
        bool(ev.location_name),
        bool(ev.location_address),
        bool(ev.price),
        bool(ev.image_url),
        bool(ev.start_time),
    ])


def _title_similarity(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, _normalize_title(a), _normalize_title(b)).ratio()


def _venue_similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a.lower().strip(), b.lower().strip()).ratio()


def _normalize_venue(name: str) -> str:
    """Normalize venue names for comparison: remove 'the', 'at', punctuation, lowercase."""
    text = name.lower().strip()
    text = re.sub(r"[^\w\s]", "", text)
    text = re.sub(r"\b(the|at|in|a)\b", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _extract_url_base(url: str) -> str | None:
    """Extract a canonical base from a URL for cross-source dedup (e.g. eventbrite event ID)."""
    if not url:
        return None
    # Eventbrite: extract numeric event ID
    m = re.search(r"eventbrite\.com/e/[^/]*-(\d{8,})", url)
    if m:
        return f"eventbrite:{m.group(1)}"
    # Ticketmaster: extract event ID
    m = re.search(r"ticketmaster\.com/event/([A-Z0-9]{16,})", url, re.IGNORECASE)
    if m:
        return f"ticketmaster:{m.group(1).upper()}"
    # Luma: extract event slug
    m = re.search(r"lu\.ma/([a-z0-9\-]{4,})", url, re.IGNORECASE)
    if m:
        return f"luma:{m.group(1).lower()}"
    return None


def _deduplicate(events: list[Event]) -> list[Event]:
    """Remove duplicate events using three passes:
    Pass 1: exact normalized-title + date key
    Pass 2: URL-based cross-source dedup (same Eventbrite/Ticketmaster/Luma event ID)
    Pass 3: fuzzy title similarity (>0.82) on same day from different sources,
            with venue normalization for near-identical titles
    """
    raw_count = len(events)

    # Pass 1: exact dedup
    seen: dict[str, Event] = {}
    for ev in events:
        key = _dedup_key(ev)
        if key not in seen:
            seen[key] = ev
        else:
            existing = seen[key]
            if _data_score(ev) > _data_score(existing):
                logger.debug(
                    "Exact dedup: keeping better copy of %r (source=%s over %s)",
                    ev.title[:40], ev.source, existing.source
                )
                seen[key] = ev
    exact_removed = raw_count - len(seen)
    deduped = list(seen.values())

    # Pass 2: URL-based cross-source dedup
    url_map: dict[str, Event] = {}
    url_removed = 0
    url_survivors: list[Event] = []
    for ev in deduped:
        url_key = _extract_url_base(ev.url or "")
        if url_key:
            if url_key not in url_map:
                url_map[url_key] = ev
            else:
                existing = url_map[url_key]
                if _data_score(ev) > _data_score(existing):
                    url_map[url_key] = ev
                logger.debug(
                    "URL dedup: merged %r [%s] with %r [%s]",
                    ev.title[:40], ev.source, existing.title[:40], existing.source
                )
                url_removed += 1
        else:
            url_survivors.append(ev)
    deduped = url_survivors + list(url_map.values())

    # Pass 3: fuzzy cross-source dedup — group by date, then pairwise check
    by_day: dict[str, list[Event]] = {}
    for ev in deduped:
        day = ev.start_time.strftime("%Y-%m-%d") if ev.start_time else "__nodatE__"
        by_day.setdefault(day, []).append(ev)

    final: list[Event] = []
    fuzzy_removed = 0
    for day, day_events in by_day.items():
        kept: list[Event] = []
        for ev in day_events:
            is_dup = False
            for existing in kept:
                if existing.source == ev.source:
                    continue  # only fuzzy-dedup across sources
                title_sim = _title_similarity(ev.title, existing.title)
                if title_sim >= 0.82:
                    # Same event from different source — keep better one
                    if _data_score(ev) > _data_score(existing):
                        logger.debug(
                            "Fuzzy dedup (title %.2f): %r [%s] replaces %r [%s]",
                            title_sim, ev.title[:40], ev.source, existing.title[:40], existing.source
                        )
                        kept.remove(existing)
                        kept.append(ev)
                    is_dup = True
                    fuzzy_removed += 1
                    break
                # Also check venue similarity for near-identical titles
                if title_sim >= 0.70 and ev.location_name and existing.location_name:
                    venue_sim = _venue_similarity(
                        _normalize_venue(ev.location_name),
                        _normalize_venue(existing.location_name)
                    )
                    if venue_sim >= 0.65:
                        if _data_score(ev) > _data_score(existing):
                            kept.remove(existing)
                            kept.append(ev)
                        is_dup = True
                        fuzzy_removed += 1
                        break
            if not is_dup:
                kept.append(ev)
        final.extend(kept)

    total_removed = raw_count - len(final)
    logger.info(
        "Dedup: %d raw → %d after dedup (%d removed: %d exact, %d url, %d fuzzy)",
        raw_count, len(final), total_removed, exact_removed, url_removed, fuzzy_removed
    )
    return final


# ── Source runner ─────────────────────────────────────────────────────────────

async def _run_source(
    name: str,
    coro,
) -> tuple[str, list[Event], str | None, float]:
    """Run a single event source, catching all exceptions. Returns (name, events, error, duration_s)."""
    t0 = time.monotonic()
    try:
        events = await coro
        duration = time.monotonic() - t0
        logger.info("Source %s returned %d events in %.1fs", name, len(events), duration)
        return name, events, None, duration
    except Exception as exc:
        duration = time.monotonic() - t0
        logger.exception("Source %s failed after %.1fs", name, duration)
        return name, [], str(exc), duration


# ── Public entry point ────────────────────────────────────────────────────────

async def _enrich_events(events: list[Event], max_enrich: int = 40) -> None:
    """Fetch detail pages for events missing description or price."""
    import httpx
    from bs4 import BeautifulSoup

    to_enrich = [e for e in events if e.url and (not e.description or not e.price)][:max_enrich]
    if not to_enrich:
        return

    logger.info("Enriching %d events missing description/price", len(to_enrich))
    headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}

    async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
        for event in to_enrich:
            try:
                resp = await client.get(event.url, headers=headers)
                if resp.status_code != 200:
                    continue
                soup = BeautifulSoup(resp.text, "lxml")

                # Description: meta description > first substantial paragraph
                if not event.description:
                    meta = soup.find("meta", attrs={"name": "description"})
                    if meta and meta.get("content"):
                        event.description = meta["content"][:300]
                    else:
                        for p in soup.find_all("p"):
                            text = p.get_text(strip=True)
                            if len(text) > 40:
                                event.description = text[:300]
                                break

                # Price: look for dollar amounts
                if not event.price:
                    price_el = soup.find(class_=lambda c: c and "price" in str(c).lower())
                    if price_el:
                        event.price = price_el.get_text(strip=True)[:50]
                    else:
                        price_match = re.search(r"\$\d[\d,.]*(?:\s*[-–]\s*\$\d[\d,.]*)?", soup.get_text())
                        if price_match:
                            event.price = price_match.group()
            except Exception:
                pass

    enriched = sum(1 for e in to_enrich if e.description or e.price)
    logger.info("Enriched %d/%d events", enriched, len(to_enrich))


async def discover_all_events(
    settings: Settings,
    newsletters: list[dict] | None = None,
    claude_client: anthropic.Anthropic | None = None,
    claude_model: str | None = None,
    spotify_artists: list[str] | None = None,
) -> tuple[list[Event], list[SourceStat], list[CostRecord], dict[str, float]]:
    """Run all event sources in parallel and return deduplicated results.

    Args:
        settings: Application settings
        newsletters: Optional list of newsletter dicts for extraction
        claude_client: Optional Anthropic client for newsletter extraction
        claude_model: Optional model ID for newsletter extraction

    Returns:
        Tuple of (events, source_stats, cost_records)
    """
    all_costs: list[CostRecord] = []

    # Build async source tasks — individual sources (no aggregation)
    tasks = [
        _run_source("Eventbrite", fetch_eventbrite(settings)),
        _run_source("Meetup", fetch_meetup(settings)),
        _run_source("Luma", fetch_luma(settings)),
        _run_source("Songkick", fetch_songkick(settings)),
        _run_source("Ticketmaster", fetch_ticketmaster(settings, spotify_artists)),
        # University — individual schools
        _run_source("MIT Events", _fetch_mit(settings)),
        _run_source("Harvard Events", _fetch_harvard(settings)),
        _run_source("Northeastern", _fetch_localist(
            "https://calendar.northeastern.edu", "Northeastern University",
            EventSource.MIT,
            "Northeastern University", "Boston, MA 02115",
        )),
        _run_source("MassArt", _fetch_localist(
            "https://calendar.massart.edu", "MassArt",
            EventSource.MIT,
            "Massachusetts College of Art and Design", "Boston, MA 02215",
        )),
        _run_source("BU Events", _fetch_localist(
            "https://butodayevents.bu.edu", "Boston University",
            EventSource.MIT,
            "Boston University", "Boston, MA 02215",
        )),
        # NOTE: Tufts (Trumba), Brandeis (DNS), Emerson (403), Wellesley (not Localist),
        # Berklee (not Localist) — all fail with Localist API. Removed until proper scrapers added.
        _run_source("Suffolk Events", _fetch_localist(
            "https://events.suffolk.edu", "Suffolk University",
            EventSource.MIT,
            "Suffolk University", "Boston, MA 02108",
        )),
        _run_source("BC Events", _fetch_localist(
            "https://events.bc.edu", "Boston College",
            EventSource.MIT,
            "Boston College", "Chestnut Hill, MA 02467",
        )),
        # Boston event sites
        _run_source("Boston Calendar", fetch_boston_events(settings)),
        _run_source("TimeOut Boston", fetch_timeout_boston(settings)),
        _run_source("Bandsintown", fetch_bandsintown(settings)),
        _run_source("Dice.fm", fetch_dice(settings)),
        _run_source("Resident Advisor", fetch_resident_advisor(settings)),
        # Museums — individual venues
        _run_source("ICA Boston", _fetch_ica(settings)),
        _run_source("MFA", _fetch_mfa(settings)),
        _run_source("MIT List Visual Arts", _fetch_mit_list(settings)),
        _run_source("Gardner Museum", _fetch_gardner(settings)),
        _run_source("Harvard Art Museums", _fetch_harvard_art(settings)),
        _run_source("Museum of Science", _fetch_mos(settings)),
        # NOTE: Athenaeum (403), ArtsEmerson (403), Brattle (404), Crossroads (JS-only) removed
        # Community / Libraries / Film
        _run_source("Boston Public Library", fetch_bpl_events(settings)),
        _run_source("Coolidge Corner", fetch_coolidge_events(settings)),
        # Music promoters
        _run_source("Bowery Presents", fetch_bowery_events(settings)),
        # Performing arts
        _run_source("BSO", fetch_bso_events(settings)),
        # Comedy
        _run_source("Improv Asylum", fetch_improv_asylum_events(settings)),
        # City events
        _run_source("Boston.gov", fetch_boston_gov_events(settings)),
        # Outdoor
        _run_source("Outdoor", fetch_outdoor_events(settings)),
    ]

    # Run all async sources concurrently
    results = await asyncio.gather(*tasks)

    # Collect events and stats
    all_events: list[Event] = []
    stats: list[SourceStat] = []
    durations: list[float] = []

    for name, events, error, duration in results:
        all_events.extend(events)
        stats.append(
            SourceStat(
                source_name=name,
                events_found=len(events),
                error_message=error,
            )
        )
        durations.append(duration)

    # Newsletter extraction (synchronous Claude calls, run in executor)
    if newsletters and claude_client and claude_model:
        try:
            logger.info("Extracting events from %d newsletters", len(newsletters))
            loop = asyncio.get_running_loop()
            nl_events, nl_costs = await loop.run_in_executor(
                None,
                extract_newsletter_events,
                newsletters,
                claude_client,
                claude_model,
            )
            all_events.extend(nl_events)
            all_costs.extend(nl_costs)
            stats.append(
                SourceStat(
                    source_name="Newsletters",
                    events_found=len(nl_events),
                )
            )
        except Exception as exc:
            logger.exception("Newsletter extraction failed")
            stats.append(
                SourceStat(
                    source_name="Newsletters",
                    events_found=0,
                    error_message=str(exc),
                )
            )

    # Enrich events missing description/price by fetching detail pages
    await _enrich_events(all_events)

    # Deduplicate
    before = len(all_events)
    all_events = _deduplicate(all_events)
    after = len(all_events)
    removed = before - after
    if removed:
        logger.info("Deduplicated events: %d -> %d (removed %d)", before, after, removed)
    # Add a synthetic dedup stat for visibility in the run detail page
    stats.append(SourceStat(
        source_name="_dedup",
        events_found=after,
        error_message=f"raw={before}, removed={removed} ({removed*100//max(before,1)}%)" if removed else None,
    ))

    # Geocode venues (adds lat/lon for distance-aware ranking & search)
    home_lat = getattr(settings, "latitude", 42.3736)
    home_lon = getattr(settings, "longitude", -71.1097)
    try:
        loop = asyncio.get_running_loop()
        all_events = await loop.run_in_executor(
            None, geocode_events, all_events, home_lat, home_lon
        )
    except Exception:
        logger.warning("Geocoding failed, continuing without coordinates")

    # Build source timing map (name -> duration)
    source_durations: dict[str, float] = {}
    for stat, dur in zip(stats, durations):
        source_durations[stat.source_name] = dur

    total = sum(s.events_found for s in stats)
    failed = sum(1 for s in stats if s.error_message)
    logger.info(
        "Event discovery complete: %d unique events from %d sources (%d failed)",
        len(all_events),
        len(stats),
        failed,
    )

    return all_events, stats, all_costs, source_durations
