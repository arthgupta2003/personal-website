"""Dice.fm event source — parses the location browse page's Next.js state.

The old api.dice.fm search endpoints are all 404 / auth-gated. The web app's
`/browse/current-location/{lat}_{lng}` page server-renders events into
`__NEXT_DATA__ → props.pageProps.events`, geo-resolved to the nearest metro hub.
"""

from __future__ import annotations

import json
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


async def fetch_dice(settings: Settings) -> list[Event]:
    """Fetch Boston-area Dice.fm events from the browse page hydration state."""
    now = datetime.now(timezone.utc)
    cutoff = now + timedelta(days=30)
    url = f"https://dice.fm/browse/current-location/{settings.latitude}_{settings.longitude}"

    async with httpx.AsyncClient(headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT, follow_redirects=True) as client:
        try:
            resp = await client.get(url)
            resp.raise_for_status()
        except Exception as exc:
            logger.warning("Dice.fm browse fetch failed: %s", exc)
            return []

    nd = BeautifulSoup(resp.text, "html.parser").select_one("#__NEXT_DATA__")
    if not nd or not nd.string:
        logger.warning("Dice.fm: __NEXT_DATA__ not found")
        return []
    try:
        items = json.loads(nd.string)["props"]["pageProps"].get("events", [])
    except (json.JSONDecodeError, KeyError):
        logger.warning("Dice.fm: failed to parse pageProps.events")
        return []

    events: list[Event] = []
    for item in items:
        title = (item.get("name") or "").strip()
        if not title:
            continue

        dates = item.get("dates") or {}
        start_time = parse_event_dt(dates.get("event_start_date") or "")
        if not start_time:
            continue
        start_aware = start_time if start_time.tzinfo else start_time.replace(tzinfo=timezone.utc)
        if start_aware < now or start_aware > cutoff:
            continue

        venues = item.get("venues") or []
        venue = venues[0] if venues else {}

        price = None
        p = item.get("price") or {}
        if p.get("amount"):
            price = f"${p['amount'] / 100:.0f}"

        lineup = (item.get("summary_lineup") or {}).get("top_artists") or []
        organizer = ", ".join(a.get("name", "") for a in lineup[:5] if a.get("name")) or None

        images = item.get("images") or {}
        image_url = images.get("landscape") or images.get("square") or images.get("portrait")

        tags = item.get("tags_types") or []
        category = tags[0].get("value") if tags and isinstance(tags[0], dict) else "music"

        perm = item.get("perm_name") or ""
        events.append(Event(
            id=make_event_id("dice", title, dates.get("event_start_date") or ""),
            source=EventSource.DICE,
            title=title,
            description=(item.get("about") or {}).get("description", "")[:500],
            url=f"https://dice.fm/event/{perm}" if perm else "",
            start_time=start_time,
            end_time=parse_event_dt(dates.get("event_end_date") or ""),
            location_name=venue.get("name", ""),
            location_address=venue.get("address", ""),
            price=price,
            organizer=organizer,
            image_url=image_url,
            category=category,
        ))

    logger.info("Dice.fm returned %d events", len(events))
    return events
