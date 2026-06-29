"""Meetup event source — parses the find page's Next.js hydration state.

Meetup's public GraphQL endpoint is auth-gated and the old CSS scrape broke, but
the `find` page is a Next.js app that ships hydrated event objects in
`__NEXT_DATA__ → props.pageProps.__APOLLO_STATE__`. We read those directly and
fan out across a few category pages to widen coverage. Online-only events are
dropped (not useful for a local recommender).
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
FIND_URL = "https://www.meetup.com/find/"
# A handful of category pages multiply yield (music, arts, tech, social, ...)
CATEGORY_IDS = [None, 511, 546, 436, 242]


def _resolve(apollo: dict, ref_holder: dict | None, *keys):
    """Resolve an Apollo {'__ref': ...} object and dig out a key."""
    if not isinstance(ref_holder, dict):
        return None
    obj = apollo.get(ref_holder["__ref"]) if "__ref" in ref_holder else ref_holder
    if not isinstance(obj, dict):
        return None
    for k in keys:
        if obj.get(k):
            return obj[k]
    return None


def _events_from_page(html: str, now, cutoff) -> list[Event]:
    soup = BeautifulSoup(html, "html.parser")
    nd = soup.select_one("#__NEXT_DATA__")
    if not nd or not nd.string:
        return []
    try:
        apollo = json.loads(nd.string)["props"]["pageProps"].get("__APOLLO_STATE__", {})
    except (json.JSONDecodeError, KeyError):
        return []

    events: list[Event] = []
    for node in apollo.values():
        if not (isinstance(node, dict) and node.get("__typename") == "Event"):
            continue
        # local recommender — skip online-only events
        if node.get("eventType") == "ONLINE":
            continue
        title = (node.get("title") or "").strip()
        if not title:
            continue
        start_time = parse_event_dt(node.get("dateTime") or "")
        if not start_time:
            continue
        start_aware = start_time if start_time.tzinfo else start_time.replace(tzinfo=timezone.utc)
        if start_aware < now or start_aware > cutoff:
            continue

        venue = node.get("venue") or {}
        addr = ", ".join(p for p in (venue.get("address"), venue.get("city"), venue.get("state")) if p)
        fee = node.get("feeSettings") or {}
        price = f"{fee['amount']} {fee['currency']}" if fee.get("amount") else None

        events.append(Event(
            id=make_event_id("meetup", title, node.get("dateTime") or ""),
            source=EventSource.MEETUP,
            title=title,
            description=(node.get("description") or "")[:500],
            url=node.get("eventUrl", ""),
            start_time=start_time,
            location_name=venue.get("name") or "",
            location_address=addr,
            is_online=False,
            price=price,
            organizer=_resolve(apollo, node.get("group"), "name"),
            image_url=_resolve(apollo, node.get("featuredEventPhoto"), "highResUrl", "baseUrl"),
            category=node.get("eventType"),
        ))
    return events


async def fetch_meetup(settings: Settings) -> list[Event]:
    """Fetch Boston-area Meetup events via the find page's hydration state."""
    now = datetime.now(timezone.utc)
    cutoff = now + timedelta(days=14)
    by_id: dict[str, Event] = {}

    async with httpx.AsyncClient(headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT, follow_redirects=True) as client:
        for cat in CATEGORY_IDS:
            params = {"location": "us--ma--Boston", "source": "EVENTS"}
            if cat:
                params["categoryId"] = cat
            try:
                resp = await client.get(FIND_URL, params=params)
                resp.raise_for_status()
            except Exception as exc:
                logger.warning("Meetup find page (cat=%s) failed: %s", cat, exc)
                continue
            for ev in _events_from_page(resp.text, now, cutoff):
                by_id[ev.id] = ev

    events = list(by_id.values())
    logger.info("Meetup returned %d events", len(events))
    return events
