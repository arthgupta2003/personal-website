"""Ticketmaster Discovery API — concerts and live events."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import httpx

from calyx.config import Settings
from calyx.events.common import make_event_id
from calyx.models import Event, EventSource, parse_event_dt

logger = logging.getLogger(__name__)

TIMEOUT = 30.0
BASE_URL = "https://app.ticketmaster.com/discovery/v2/events.json"





async def _fetch_events_page(
    client: httpx.AsyncClient,
    api_key: str,
    lat: float,
    lon: float,
    radius: int,
    start_date: str,
    end_date: str,
    page: int = 0,
    keyword: str | None = None,
) -> dict:
    params = {
        "apikey": api_key,
        "latlong": f"{lat},{lon}",
        "radius": str(radius),
        "unit": "miles",
        "startDateTime": start_date,
        "endDateTime": end_date,
        "size": "100",
        "page": str(page),
        "sort": "date,asc",
    }
    if keyword:
        params["keyword"] = keyword

    resp = await client.get(BASE_URL, params=params)
    resp.raise_for_status()
    return resp.json()


def _parse_event(ev: dict) -> Event | None:
    title = ev.get("name", "")
    if not title:
        return None

    # Date/time
    dates = ev.get("dates", {}).get("start", {})
    date_str = dates.get("dateTime", "") or dates.get("localDate", "")
    start_time = parse_event_dt(date_str)

    end_dates = ev.get("dates", {}).get("end", {})
    end_str = end_dates.get("dateTime", "") if end_dates else ""
    end_time = parse_event_dt(end_str)

    # Venue
    venues = ev.get("_embedded", {}).get("venues", [])
    venue_name = ""
    venue_address = ""
    if venues:
        v = venues[0]
        venue_name = v.get("name", "")
        addr = v.get("address", {})
        city = v.get("city", {}).get("name", "")
        state = v.get("state", {}).get("stateCode", "")
        line1 = addr.get("line1", "")
        venue_address = f"{line1}, {city}, {state}".strip(", ")

    # Price
    price = None
    price_ranges = ev.get("priceRanges", [])
    if price_ranges:
        pr = price_ranges[0]
        min_p = pr.get("min", 0)
        max_p = pr.get("max", 0)
        if min_p and max_p:
            price = f"${min_p:.0f}-${max_p:.0f}"
        elif min_p:
            price = f"From ${min_p:.0f}"

    # URL
    url = ev.get("url", "")

    # Performers
    attractions = ev.get("_embedded", {}).get("attractions", [])
    performers = [a.get("name", "") for a in attractions if a.get("name")]
    organizer = ", ".join(performers) if performers else None

    # Category
    classifications = ev.get("classifications", [])
    category = None
    if classifications:
        segment = classifications[0].get("segment", {}).get("name", "")
        genre = classifications[0].get("genre", {}).get("name", "")
        category = f"{segment}: {genre}" if genre and genre != "Undefined" else segment

    # Image
    images = ev.get("images", [])
    image_url = images[0].get("url") if images else None

    return Event(
        id=make_event_id("ticketmaster", title, date_str),
        source=EventSource.SONGKICK,  # reuse SONGKICK slot for concerts
        title=title,
        description=f"Performers: {organizer}" if organizer else "",
        url=url,
        start_time=start_time,
        end_time=end_time,
        location_name=venue_name,
        location_address=venue_address,
        price=price,
        category=category,
        organizer=organizer,
        image_url=image_url,
    )


async def fetch_ticketmaster(
    settings: Settings,
    spotify_artists: list[str] | None = None,
) -> list[Event]:
    """Fetch events from Ticketmaster Discovery API.

    Performs a general location search, plus targeted searches for Spotify artists.
    """
    if not settings.ticketmaster_api_key:
        logger.info("Ticketmaster API key not configured — skipping")
        return []

    now = datetime.now(timezone.utc)
    start = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    end = (now + timedelta(days=14)).strftime("%Y-%m-%dT%H:%M:%SZ")
    radius = 15  # miles

    events: list[Event] = []
    seen_ids: set[str] = set()

    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        # General location search
        try:
            data = await _fetch_events_page(
                client, settings.ticketmaster_api_key,
                settings.latitude, settings.longitude, radius,
                start, end,
            )
            for ev in data.get("_embedded", {}).get("events", []):
                parsed = _parse_event(ev)
                if parsed and parsed.id not in seen_ids:
                    events.append(parsed)
                    seen_ids.add(parsed.id)

            # Get page 2 if available
            total_pages = data.get("page", {}).get("totalPages", 1)
            if total_pages > 1:
                data2 = await _fetch_events_page(
                    client, settings.ticketmaster_api_key,
                    settings.latitude, settings.longitude, radius,
                    start, end, page=1,
                )
                for ev in data2.get("_embedded", {}).get("events", []):
                    parsed = _parse_event(ev)
                    if parsed and parsed.id not in seen_ids:
                        events.append(parsed)
                        seen_ids.add(parsed.id)
        except Exception:
            logger.exception("Ticketmaster general search failed")

        # Artist-specific searches (top 20 Spotify artists)
        if spotify_artists:
            for artist in spotify_artists[:20]:
                try:
                    data = await _fetch_events_page(
                        client, settings.ticketmaster_api_key,
                        settings.latitude, settings.longitude, 50,  # wider radius for specific artists
                        start, (now + timedelta(days=60)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                        keyword=artist,
                    )
                    for ev in data.get("_embedded", {}).get("events", []):
                        parsed = _parse_event(ev)
                        if parsed and parsed.id not in seen_ids:
                            # Tag as artist match in description
                            if not parsed.description:
                                parsed.description = f"Spotify artist match: {artist}"
                            events.append(parsed)
                            seen_ids.add(parsed.id)
                except Exception:
                    # Rate limit or no results — fine
                    continue

    logger.info("Ticketmaster returned %d events", len(events))
    return events
