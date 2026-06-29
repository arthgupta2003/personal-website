"""University event sources — Localist (MIT, Northeastern, ...) and Trumba (Harvard, Tufts, ...)."""

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





# ── Harvard Events (Trumba JSON API) ─────────────────────────────────────────

async def _fetch_harvard(settings: Settings) -> list[Event]:
    """Fetch Harvard Gazette events from the Trumba JSON calendar API."""
    url = "https://www.trumba.com/calendars/gazette.json"
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}

    events: list[Event] = []
    now = datetime.now(timezone.utc)
    cutoff = now + timedelta(days=14)

    async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True) as client:
        resp = await client.get(url, headers=headers)
        resp.raise_for_status()

    data = resp.json()
    if not isinstance(data, list):
        logger.warning("Harvard Trumba API returned unexpected type: %s", type(data))
        return events

    for item in data:
        # Skip canceled events
        if item.get("canceled"):
            continue

        title = (item.get("title") or "").strip()
        if not title:
            continue

        # Parse start/end datetimes (ISO format like "2026-03-08T15:00:00")
        start_raw = item.get("startDateTime")
        end_raw = item.get("endDateTime")
        start_time = parse_event_dt(start_raw)
        end_time = parse_event_dt(end_raw)

        # Filter: only events starting within the next 10 days
        if start_time:
            # Make naive datetimes comparable with UTC now
            start_aware = (
                start_time if start_time.tzinfo else start_time.replace(tzinfo=timezone.utc)
            )
            if start_aware < now or start_aware > cutoff:
                continue
        else:
            # Skip events with no parseable start time
            continue

        # Extract plain-text location from HTML location field
        location_html = item.get("location") or ""
        if "<" in location_html:
            location = BeautifulSoup(location_html, "html.parser").get_text(strip=True)
        else:
            location = location_html.strip()

        description = (item.get("description") or "")[:500]
        event_url = item.get("permaLinkUrl") or ""

        # Image
        detail_image = item.get("detailImage") or {}
        image_url = detail_image.get("url") if isinstance(detail_image, dict) else None

        # Categories from customFields
        category = None
        custom_fields = item.get("customFields")
        if isinstance(custom_fields, list):
            for cf in custom_fields:
                if isinstance(cf, dict) and "category" in (cf.get("fieldName") or "").lower():
                    category = cf.get("value")
                    break

        date_str = start_raw or ""
        events.append(
            Event(
                id=make_event_id("harvard", title, date_str),
                source=EventSource.HARVARD,
                title=title,
                description=description,
                url=event_url,
                start_time=start_time,
                end_time=end_time,
                location_name=location or "Harvard Campus",
                location_address="Cambridge, MA",
                organizer="Harvard University",
                category=category,
                image_url=image_url,
            )
        )

    logger.info("Harvard Trumba API returned %d events", len(events))
    return events


# ── Localist API (Northeastern, MassArt) ─────────────────────────────────────

async def _fetch_localist(
    base_url: str,
    school_name: str,
    source: EventSource,
    location_default: str,
    address_default: str,
) -> list[Event]:
    """Generic Localist API fetcher. Covers Northeastern, MassArt, etc."""
    now = datetime.now(timezone.utc)
    start_str = now.strftime("%Y-%m-%d")
    end_str = (now + timedelta(days=30)).strftime("%Y-%m-%d")
    events: list[Event] = []

    async with httpx.AsyncClient(headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT, follow_redirects=True) as client:
        page = 1
        while page <= 5:  # max 5 pages = 500 events
            try:
                resp = await client.get(
                    f"{base_url}/api/2/events",
                    params={"pp": 100, "start": start_str, "end": end_str, "page": page},
                )
                resp.raise_for_status()
                data = resp.json()
            except Exception as exc:
                logger.warning("%s Localist page %d failed: %s", school_name, page, exc)
                break

            items = data.get("events", [])
            if not items:
                break

            for item in items:
                evt = item.get("event", item)
                title = (evt.get("title") or "").strip()
                if not title:
                    continue
                description = (evt.get("description_text") or evt.get("description") or "")[:500]
                url = evt.get("url") or evt.get("localist_url") or ""
                location = (evt.get("location_name") or evt.get("venue", {}).get("name") or location_default) if isinstance(evt.get("venue"), dict) else location_default
                address = (evt.get("address") or address_default)
                image_url = None
                if evt.get("photo"):
                    photo = evt["photo"]
                    if isinstance(photo, dict):
                        image_url = photo.get("url") or photo.get("medium") or photo.get("small")
                    elif isinstance(photo, str):
                        image_url = photo

                # Parse instances
                instances = evt.get("event_instances", [])
                start_time = None
                end_time = None
                if instances:
                    inst = instances[0].get("event_instance", instances[0])
                    start_raw = inst.get("start")
                    end_raw = inst.get("end")
                    if start_raw:
                        start_time = parse_event_dt(start_raw)
                    if end_raw:
                        end_time = parse_event_dt(end_raw)
                else:
                    start_raw = evt.get("first_date") or ""
                    start_time = parse_event_dt(start_raw) if start_raw else None

                date_str = str(evt.get("first_date") or "")
                events.append(Event(
                    id=make_event_id(school_name.lower().replace(" ", "_"), title, date_str),
                    source=source,
                    title=title,
                    description=description,
                    url=url,
                    start_time=start_time,
                    end_time=end_time,
                    location_name=location,
                    location_address=address,
                    organizer=school_name,
                    image_url=image_url,
                ))

            if page >= data.get("total_pages", 1):
                break
            page += 1

    logger.info("%s Localist returned %d events", school_name, len(events))
    return events


# ── Additional Trumba sources ─────────────────────────────────────────────────

async def _fetch_trumba_school(
    calendar_name: str,
    school_name: str,
    source: EventSource,
    location_default: str,
    address_default: str,
) -> list[Event]:
    """Fetch a school's Trumba JSON calendar (same shape as Harvard's gazette feed).

    The endpoint ignores startDate/endDate query params, so we fetch the full
    feed and filter to the next 30 days client-side.
    """
    now = datetime.now(timezone.utc)
    cutoff = now + timedelta(days=30)
    url = f"https://www.trumba.com/calendars/{calendar_name}.json"

    events: list[Event] = []
    async with httpx.AsyncClient(headers={"User-Agent": USER_AGENT, "Accept": "application/json"}, timeout=TIMEOUT, follow_redirects=True) as client:
        try:
            resp = await client.get(url)
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            logger.warning("%s Trumba fetch failed: %s", school_name, exc)
            return []

    for item in data if isinstance(data, list) else []:
        if item.get("canceled"):
            continue
        title = (item.get("title") or "").strip()
        if not title:
            continue
        start_raw = item.get("startDateTime") or item.get("startDate") or ""
        start_time = parse_event_dt(start_raw) if start_raw else None
        if not start_time:
            continue
        start_aware = start_time if start_time.tzinfo else start_time.replace(tzinfo=timezone.utc)
        if start_aware < now or start_aware > cutoff:
            continue

        end_raw = item.get("endDateTime") or item.get("endDate") or ""
        description = (item.get("description") or "")
        if "<" in description:
            description = BeautifulSoup(description, "html.parser").get_text(" ", strip=True)
        event_url = item.get("permaLinkUrl") or item.get("webLink") or ""

        location_raw = item.get("location") or ""
        location = (
            BeautifulSoup(location_raw, "html.parser").get_text(strip=True)
            if "<" in str(location_raw) else str(location_raw)
        ) or location_default

        events.append(Event(
            id=make_event_id(school_name.lower().replace(" ", "_"), title, start_raw),
            source=source,
            title=title,
            description=description[:500],
            url=event_url,
            start_time=start_time,
            end_time=parse_event_dt(end_raw) if end_raw else None,
            location_name=location,
            location_address=address_default,
            organizer=school_name,
        ))

    logger.info("%s Trumba returned %d events", school_name, len(events))
    return events


