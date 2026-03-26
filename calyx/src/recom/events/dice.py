"""Dice.fm event source — public API, no key required."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import httpx

from recom.config import Settings
from recom.events.common import make_event_id
from recom.models import Event, EventSource, EASTERN

logger = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
TIMEOUT = 30.0



def _parse_dice_date(date_str: str) -> datetime | None:
    if not date_str:
        return None
    # Dice uses ISO 8601 with ms and Z
    for suffix in ("Z", ""):
        s = date_str.strip().rstrip("Z")
        for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
            try:
                dt = datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
                return dt.astimezone(EASTERN)
            except ValueError:
                pass
    return None


async def fetch_dice(settings: Settings) -> list[Event]:
    """Fetch Boston events from Dice.fm public API."""
    now = datetime.now(timezone.utc)
    cutoff = now + timedelta(days=14)

    # Dice public search API
    url = "https://api.dice.fm/api/v1/events"
    params = {
        "types[]": "linkout,event",
        "filter[location]": "Boston, MA",
        "filter[from]": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "filter[to]": cutoff.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "filter[radius_in_meters]": "40000",
        "page[size]": "50",
        "sort": "date",
    }
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
        "x-api-key": "dice-public",
    }

    events: list[Event] = []

    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            resp = await client.get(url, params=params, headers=headers)

            if resp.status_code not in (200, 201):
                # Try alternate endpoint
                alt_url = "https://api.dice.fm/api/v2/events/search"
                alt_params = {
                    "query": "Boston",
                    "location_lat": str(settings.latitude),
                    "location_lng": str(settings.longitude),
                    "radius_km": "25",
                    "from_date": now.strftime("%Y-%m-%d"),
                    "to_date": cutoff.strftime("%Y-%m-%d"),
                    "limit": "50",
                }
                resp = await client.get(alt_url, params=alt_params, headers=headers)
                if resp.status_code not in (200, 201):
                    logger.info("Dice.fm API returned %d — skipping", resp.status_code)
                    return []

            data = resp.json()

        # Handle different response shapes
        items = []
        if isinstance(data, list):
            items = data
        elif isinstance(data, dict):
            items = data.get("data", data.get("events", data.get("items", [])))

        for item in items:
            # Unwrap if nested
            if isinstance(item, dict) and "event" in item:
                item = item["event"]

            title = item.get("name") or item.get("title") or ""
            if not title:
                continue

            date_str = (
                item.get("date") or
                item.get("start_date") or
                item.get("event_date") or
                item.get("starts_at") or ""
            )
            start_time = _parse_dice_date(date_str)

            venue = item.get("venue") or {}
            venue_name = venue.get("name", "") if isinstance(venue, dict) else str(venue)
            venue_addr = ""
            if isinstance(venue, dict):
                venue_addr = venue.get("address", "") or venue.get("city", "")

            # Price
            price = None
            ticket_types = item.get("ticket_types") or []
            if isinstance(ticket_types, list) and ticket_types:
                prices = [t.get("price", {}) for t in ticket_types if isinstance(t, dict)]
                amounts = [p.get("face_value", p.get("total", 0)) for p in prices if isinstance(p, dict) and p]
                amounts = [a for a in amounts if a]
                if amounts:
                    min_p = min(amounts) / 100  # Dice stores in cents
                    price = f"From ${min_p:.0f}"
            if not price:
                raw_price = item.get("price") or item.get("min_price")
                if raw_price:
                    price = str(raw_price)

            # Lineup
            lineup = item.get("lineup_details") or item.get("artists") or []
            organizer = None
            if isinstance(lineup, list) and lineup:
                names = []
                for l in lineup[:5]:
                    if isinstance(l, dict):
                        names.append(l.get("name", l.get("artist_name", "")))
                    elif isinstance(l, str):
                        names.append(l)
                organizer = ", ".join(n for n in names if n) or None

            image_url = None
            images = item.get("images") or item.get("image")
            if isinstance(images, list) and images:
                image_url = images[0].get("url") if isinstance(images[0], dict) else images[0]
            elif isinstance(images, str):
                image_url = images

            evt_url = item.get("url") or item.get("dice_url") or ""
            if evt_url and not evt_url.startswith("http"):
                evt_url = f"https://dice.fm/event/{evt_url}"

            events.append(Event(
                id=make_event_id("dice", title, date_str),
                source=EventSource.DICE,
                title=title,
                description=item.get("description", "")[:500],
                url=evt_url,
                start_time=start_time,
                location_name=venue_name,
                location_address=venue_addr,
                price=price,
                organizer=organizer,
                image_url=image_url,
                category=item.get("genre") or item.get("category") or "music",
            ))

    except Exception:
        logger.exception("Dice.fm fetch failed")

    logger.info("Dice.fm returned %d events", len(events))
    return events
