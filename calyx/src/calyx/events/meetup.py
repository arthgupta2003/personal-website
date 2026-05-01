"""Meetup event source — GraphQL API with scrape fallback."""

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
GRAPHQL_URL = "https://api.meetup.com/gql"





# ── GraphQL API path ──────────────────────────────────────────────────────────

RANKED_EVENTS_QUERY = """
query($lat: Float!, $lon: Float!, $startDateRange: DateTime, $endDateRange: DateTime) {
  rankedEvents(
    filter: {
      lat: $lat
      lon: $lon
      radius: 40
      startDateRange: $startDateRange
      endDateRange: $endDateRange
    }
    first: 50
  ) {
    edges {
      node {
        id
        title
        description
        eventUrl
        dateTime
        endTime
        going
        isOnline
        venue {
          name
          address
          city
          state
        }
        group {
          name
        }
        imageUrl
        eventType
        feeSettings {
          amount
          currency
        }
      }
    }
  }
}
"""


async def _fetch_via_graphql(settings: Settings) -> list[Event]:
    now = datetime.now(timezone.utc)
    end = now + timedelta(days=14)

    variables = {
        "lat": settings.latitude,
        "lon": settings.longitude,
        "startDateRange": now.strftime("%Y-%m-%dT%H:%M:%S-04:00"),
        "endDateRange": end.strftime("%Y-%m-%dT%H:%M:%S-04:00"),
    }
    payload = {"query": RANKED_EVENTS_QUERY, "variables": variables}
    headers = {"User-Agent": USER_AGENT, "Content-Type": "application/json"}

    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        resp = await client.post(GRAPHQL_URL, json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()

    events: list[Event] = []
    edges = (data.get("data") or {}).get("rankedEvents", {}).get("edges", [])
    for edge in edges:
        node = edge.get("node", {})
        title = node.get("title", "")
        if not title:
            continue

        venue = node.get("venue") or {}
        addr_parts = [venue.get("address", ""), venue.get("city", ""), venue.get("state", "")]
        addr = ", ".join(p for p in addr_parts if p)

        fee = node.get("feeSettings") or {}
        price = f"{fee['amount']} {fee['currency']}" if fee.get("amount") else None

        dt_raw = node.get("dateTime", "")
        events.append(
            Event(
                id=make_event_id("meetup", title,dt_raw),
                source=EventSource.MEETUP,
                title=title,
                description=(node.get("description") or "")[:500],
                url=node.get("eventUrl", ""),
                start_time=parse_event_dt(dt_raw),
                end_time=parse_event_dt(node.get("endTime")),
                location_name=venue.get("name", ""),
                location_address=addr,
                is_online=node.get("isOnline", False),
                price=price,
                attendee_count=node.get("going"),
                organizer=(node.get("group") or {}).get("name"),
                image_url=node.get("imageUrl"),
                category=node.get("eventType"),
            )
        )
    return events


# ── Scrape fallback ───────────────────────────────────────────────────────────

async def _fetch_via_scrape(settings: Settings) -> list[Event]:
    url = "https://www.meetup.com/find/?location=Cambridge%2C+MA&source=EVENTS"
    headers = {"User-Agent": USER_AGENT}

    async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True) as client:
        resp = await client.get(url, headers=headers)
        resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "lxml")
    events: list[Event] = []

    cards = soup.select(
        "div[data-testid='categoryResults-eventCard'], "
        "div.eventCard, "
        "a[data-event-label]"
    )
    if not cards:
        cards = soup.select("div[id*='event']")

    for card in cards:
        link_tag = card.find("a", href=True) if card.name != "a" else card
        event_url = link_tag["href"] if link_tag else ""
        if event_url and not event_url.startswith("http"):
            event_url = "https://www.meetup.com" + event_url

        title_tag = card.find(["h2", "h3", "span"], class_=lambda c: c and "title" in str(c).lower()) or card.find(["h2", "h3"])
        title = title_tag.get_text(strip=True) if title_tag else ""
        if not title:
            continue

        time_tag = card.find("time")
        date_str = ""
        if time_tag:
            date_str = time_tag.get("datetime", "") or time_tag.get_text(strip=True)
        start_time = parse_event_dt(date_str)

        location_tag = card.find("p", class_=lambda c: c and "venue" in str(c).lower())
        location = location_tag.get_text(strip=True) if location_tag else ""

        attendee_tag = card.find("span", class_=lambda c: c and "attendee" in str(c).lower())
        attendee_count = None
        if attendee_tag:
            try:
                attendee_count = int("".join(c for c in attendee_tag.get_text() if c.isdigit()))
            except ValueError:
                pass

        img_tag = card.find("img", src=True)
        image_url = img_tag["src"] if img_tag else None

        events.append(
            Event(
                id=make_event_id("meetup", title,date_str),
                source=EventSource.MEETUP,
                title=title,
                url=event_url,
                start_time=start_time,
                location_name=location,
                attendee_count=attendee_count,
                image_url=image_url,
            )
        )

    return events


# ── Public entry point ────────────────────────────────────────────────────────

async def fetch_meetup(settings: Settings) -> list[Event]:
    """Fetch events from Meetup (GraphQL API, scrape fallback)."""
    try:
        logger.info("Fetching Meetup events via GraphQL API")
        return await _fetch_via_graphql(settings)
    except Exception:
        logger.warning("Meetup GraphQL API failed, falling back to scrape", exc_info=True)
    try:
        return await _fetch_via_scrape(settings)
    except Exception:
        logger.exception("Meetup scrape fallback also failed")
        return []
