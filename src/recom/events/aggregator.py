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
from recom.events.museums import fetch_museum_events
from recom.events.newsletters import extract_newsletter_events
from recom.events.outdoor import fetch_outdoor_events
from recom.events.songkick import fetch_songkick
from recom.events.ticketmaster import fetch_ticketmaster
from recom.events.timeout_boston import fetch_timeout_boston
from recom.events.university import fetch_university_events
from recom.models import CostRecord, Event, SourceStat

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


def _deduplicate(events: list[Event]) -> list[Event]:
    """Remove duplicate events using exact key match + fuzzy cross-source dedup.

    Pass 1: exact normalized-title + date key (catches same event, same source wording)
    Pass 2: fuzzy title similarity (>0.82) on same day from different sources
    """
    # Pass 1: exact dedup
    seen: dict[str, Event] = {}
    for ev in events:
        key = _dedup_key(ev)
        if key not in seen:
            seen[key] = ev
        else:
            existing = seen[key]
            if _data_score(ev) > _data_score(existing):
                seen[key] = ev
    deduped = list(seen.values())

    # Pass 2: fuzzy cross-source dedup — group by date, then pairwise check
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
                        kept.remove(existing)
                        kept.append(ev)
                    is_dup = True
                    fuzzy_removed += 1
                    break
                # Also check venue similarity for near-identical titles
                if title_sim >= 0.70 and ev.location_name and existing.location_name:
                    if _venue_similarity(ev.location_name, existing.location_name) >= 0.70:
                        if _data_score(ev) > _data_score(existing):
                            kept.remove(existing)
                            kept.append(ev)
                        is_dup = True
                        fuzzy_removed += 1
                        break
            if not is_dup:
                kept.append(ev)
        final.extend(kept)

    if fuzzy_removed:
        logger.info("Fuzzy dedup removed %d cross-source duplicates", fuzzy_removed)

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

    # Build async source tasks
    tasks = [
        _run_source("Eventbrite", fetch_eventbrite(settings)),
        _run_source("Meetup", fetch_meetup(settings)),
        _run_source("Luma", fetch_luma(settings)),
        _run_source("Songkick", fetch_songkick(settings)),
        _run_source("Ticketmaster", fetch_ticketmaster(settings, spotify_artists)),
        _run_source("University", fetch_university_events(settings)),
        _run_source("Boston Calendar", fetch_boston_events(settings)),
        _run_source("TimeOut Boston", fetch_timeout_boston(settings)),
        _run_source("Bandsintown", fetch_bandsintown(settings)),
        _run_source("Dice.fm", fetch_dice(settings)),
        _run_source("Resident Advisor", fetch_resident_advisor(settings)),
        _run_source("Museums", fetch_museum_events(settings)),
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

    # Deduplicate
    before = len(all_events)
    all_events = _deduplicate(all_events)
    after = len(all_events)
    if before != after:
        logger.info("Deduplicated events: %d -> %d (removed %d)", before, after, before - after)

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
