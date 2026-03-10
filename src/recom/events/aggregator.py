"""Event aggregator — runs all sources in parallel, deduplicates results."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import unicodedata
from datetime import datetime

import anthropic

from recom.config import Settings
from recom.events.boston_calendar import fetch_boston_events
from recom.events.eventbrite import fetch_eventbrite
from recom.events.luma import fetch_luma
from recom.events.meetup import fetch_meetup
from recom.events.newsletters import extract_newsletter_events
from recom.events.songkick import fetch_songkick
from recom.events.ticketmaster import fetch_ticketmaster
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


def _deduplicate(events: list[Event]) -> list[Event]:
    """Remove duplicate events (same normalized title on same day).

    When duplicates exist, prefer the event with more populated fields.
    """
    seen: dict[str, Event] = {}
    for ev in events:
        key = _dedup_key(ev)
        if key not in seen:
            seen[key] = ev
        else:
            existing = seen[key]
            # Keep the one with more data
            new_score = sum([
                bool(ev.description),
                bool(ev.url),
                bool(ev.location_name),
                bool(ev.location_address),
                bool(ev.price),
                bool(ev.image_url),
                bool(ev.start_time),
            ])
            old_score = sum([
                bool(existing.description),
                bool(existing.url),
                bool(existing.location_name),
                bool(existing.location_address),
                bool(existing.price),
                bool(existing.image_url),
                bool(existing.start_time),
            ])
            if new_score > old_score:
                seen[key] = ev
    return list(seen.values())


# ── Source runner ─────────────────────────────────────────────────────────────

async def _run_source(
    name: str,
    coro,
) -> tuple[str, list[Event], str | None]:
    """Run a single event source, catching all exceptions."""
    try:
        events = await coro
        logger.info("Source %s returned %d events", name, len(events))
        return name, events, None
    except Exception as exc:
        logger.exception("Source %s failed", name)
        return name, [], str(exc)


# ── Public entry point ────────────────────────────────────────────────────────

async def discover_all_events(
    settings: Settings,
    newsletters: list[dict] | None = None,
    claude_client: anthropic.Anthropic | None = None,
    claude_model: str | None = None,
    spotify_artists: list[str] | None = None,
) -> tuple[list[Event], list[SourceStat], list[CostRecord]]:
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
    ]

    # Run all async sources concurrently
    results = await asyncio.gather(*tasks)

    # Collect events and stats
    all_events: list[Event] = []
    stats: list[SourceStat] = []

    for name, events, error in results:
        all_events.extend(events)
        stats.append(
            SourceStat(
                source_name=name,
                events_found=len(events),
                error_message=error,
            )
        )

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

    total = sum(s.events_found for s in stats)
    failed = sum(1 for s in stats if s.error_message)
    logger.info(
        "Event discovery complete: %d unique events from %d sources (%d failed)",
        len(all_events),
        len(stats),
        failed,
    )

    return all_events, stats, all_costs
