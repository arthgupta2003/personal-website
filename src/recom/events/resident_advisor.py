"""Resident Advisor event source — public GraphQL API, electronic/club music."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import httpx

from recom.config import Settings
from recom.events.common import make_event_id
from recom.models import EASTERN, Event, EventSource

logger = logging.getLogger(__name__)

TIMEOUT = 30.0
# RA area IDs: 6=Boston/New England, try broader if few results
RA_AREA_IDS = [6]

GRAPHQL_QUERY = """
query EventListings($filters: FilterInputDtoInput, $pageSize: Int) {
  eventListings(filters: $filters, pageSize: $pageSize) {
    data {
      id
      listingDate
      event {
        id
        title
        date
        startTime
        endTime
        contentUrl
        venue { name address }
        artists { name }
        images { filename }
      }
    }
  }
}
"""



def _parse_ra_date(date_str: str, time_str: str | None = None) -> datetime | None:
    """Parse RA's ISO date format (2026-03-14T00:00:00.000)."""
    if not date_str:
        return None
    # Use time_str if available (it's the actual start time)
    raw = time_str or date_str
    raw = raw.strip()
    for fmt in (
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d",
    ):
        try:
            dt = datetime.strptime(raw[:len(fmt) + 4], fmt)
            return dt.replace(tzinfo=EASTERN)
        except ValueError:
            pass
    return None


async def fetch_resident_advisor(settings: Settings) -> list[Event]:
    """Fetch Boston area events from Resident Advisor GraphQL API."""
    now = datetime.now(timezone.utc)
    end = now + timedelta(days=14)

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Referer": "https://ra.co/events",
        "Origin": "https://ra.co",
    }

    events: list[Event] = []
    seen: set[str] = set()

    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        for area_id in RA_AREA_IDS:
            payload = {
                "query": GRAPHQL_QUERY,
                "variables": {
                    "pageSize": 50,
                    "filters": {
                        "areas": {"eq": area_id},
                        "listingDate": {
                            "gte": now.strftime("%Y-%m-%d"),
                            "lte": end.strftime("%Y-%m-%d"),
                        },
                    },
                },
            }
            try:
                resp = await client.post(
                    "https://ra.co/graphql",
                    json=payload,
                    headers=headers,
                )
                resp.raise_for_status()
                data = resp.json()

                if "errors" in data:
                    logger.warning("RA GraphQL errors: %s", data["errors"][:1])
                    continue

                listings = (
                    data.get("data", {})
                    .get("eventListings", {})
                    .get("data", [])
                )

                for listing in listings:
                    ev = listing.get("event") or {}
                    title = ev.get("title", "")
                    if not title or title.lower() in seen:
                        continue
                    seen.add(title.lower())

                    date_raw = ev.get("date", "")
                    time_raw = ev.get("startTime", "")
                    end_raw = ev.get("endTime", "")
                    start_time = _parse_ra_date(date_raw, time_raw)
                    end_time = _parse_ra_date(date_raw, end_raw)

                    venue = ev.get("venue") or {}
                    venue_name = venue.get("name", "")
                    venue_addr = venue.get("address", "")

                    artists = ev.get("artists") or []
                    organizer = None
                    if artists:
                        names = [a.get("name", "") for a in artists[:5] if isinstance(a, dict)]
                        organizer = ", ".join(n for n in names if n) or None

                    content_url = ev.get("contentUrl", "")
                    evt_url = f"https://ra.co/events/{content_url}" if content_url else ""

                    images = ev.get("images") or []
                    image_url = None
                    if images and isinstance(images[0], dict):
                        fname = images[0].get("filename", "")
                        if fname:
                            image_url = f"https://images.ra.co/images/{fname}"

                    events.append(Event(
                        id=make_event_id("ra", title, date_raw),
                        source=EventSource.RESIDENT_ADVISOR,
                        title=title,
                        url=evt_url,
                        start_time=start_time,
                        end_time=end_time,
                        location_name=venue_name,
                        location_address=venue_addr,
                        category="music",
                        organizer=organizer,
                        image_url=image_url,
                    ))

            except Exception:
                logger.exception("Resident Advisor fetch failed for area %d", area_id)

    logger.info("Resident Advisor returned %d events", len(events))
    return events
