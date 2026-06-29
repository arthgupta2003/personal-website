"""Bandsintown event source — per-artist endpoint, seeded from Spotify.

The city/discover endpoints are auth-walled (403). The per-artist endpoint works
unauthenticated with Bandsintown's public JS-widget app_id, so we fan out over
the user's Spotify artists and keep their MA-area shows.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import httpx

from calyx.config import Settings
from calyx.events.common import make_event_id
from calyx.models import Event, EventSource, parse_event_dt

logger = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
TIMEOUT = 20.0
APP_ID = "js_127.0.0.1"  # Bandsintown's public JS-widget app_id (unauthenticated)
MAX_ARTISTS = 40


async def _fetch_artist(client: httpx.AsyncClient, artist: str, now, cutoff) -> list[Event]:
    url = f"https://rest.bandsintown.com/artists/{quote(artist, safe='')}/events"
    try:
        resp = await client.get(url, params={"app_id": APP_ID}, headers={"Accept": "application/json"})
        if resp.status_code != 200:
            return []
        data = resp.json()
    except Exception:
        return []
    if not isinstance(data, list):
        return []

    events: list[Event] = []
    for item in data:
        venue = item.get("venue") or {}
        if (venue.get("region") or "").upper() != "MA":
            continue
        start_time = parse_event_dt(item.get("datetime") or "")
        if not start_time:
            continue
        start_aware = start_time if start_time.tzinfo else start_time.replace(tzinfo=timezone.utc)
        if start_aware < now or start_aware > cutoff:
            continue

        lineup = item.get("lineup") or []
        title = (item.get("title") or "").strip() or " / ".join(lineup[:3]) or artist
        addr = ", ".join(p for p in (
            venue.get("name"), venue.get("city"), venue.get("region"), venue.get("postal_code"),
        ) if p)
        offers = item.get("offers") or []
        url_out = item.get("url") or (offers[0].get("url") if offers else "")

        events.append(Event(
            id=make_event_id("bandsintown", title, item.get("datetime") or ""),
            source=EventSource.BANDSINTOWN,
            title=title,
            description=(item.get("description") or "")[:300],
            url=url_out,
            start_time=start_time,
            location_name=venue.get("name", ""),
            location_address=addr,
            organizer=", ".join(lineup[:5]) or artist,
            image_url=(item.get("artist") or {}).get("image_url"),
            category="music",
        ))
    return events


async def fetch_bandsintown(settings: Settings, spotify_artists: list[str] | None = None) -> list[Event]:
    """Fetch MA-area shows for the user's Spotify artists via Bandsintown."""
    if not spotify_artists:
        logger.info("Bandsintown: no Spotify artists to seed — skipping")
        return []

    now = datetime.now(timezone.utc)
    cutoff = now + timedelta(days=60)
    artists = spotify_artists[:MAX_ARTISTS]

    async with httpx.AsyncClient(headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT, follow_redirects=True) as client:
        results = await asyncio.gather(
            *(_fetch_artist(client, a, now, cutoff) for a in artists),
            return_exceptions=True,
        )

    by_id: dict[str, Event] = {}
    for r in results:
        if isinstance(r, list):
            for ev in r:
                by_id[ev.id] = ev

    events = list(by_id.values())
    logger.info("Bandsintown returned %d events (from %d artists)", len(events), len(artists))
    return events
