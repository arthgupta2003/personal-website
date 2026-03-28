"""Luma event source — undocumented API with scrape fallback."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import httpx
from bs4 import BeautifulSoup

from recom.config import Settings
from recom.events.common import make_event_id
from recom.models import Event, EventSource, parse_event_dt

logger = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
TIMEOUT = 30.0





# ── Undocumented API path ────────────────────────────────────────────────────

async def _fetch_via_api(settings: Settings) -> list[Event]:
    now = datetime.now(timezone.utc)
    end = now + timedelta(days=14)

    params = {
        "geo_latitude": str(settings.latitude),
        "geo_longitude": str(settings.longitude),
        "geo_radius": "40000",  # metres
        "start_at_min": now.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "start_at_max": end.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "pagination_limit": "50",
    }
    headers = {"User-Agent": USER_AGENT}

    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        resp = await client.get(
            "https://api.lu.ma/discover/get-paginated-events",
            params=params,
            headers=headers,
        )
        resp.raise_for_status()
        data = resp.json()

    events: list[Event] = []
    for entry in data.get("entries", []):
        ev = entry.get("event") or entry
        title = ev.get("name", "")
        if not title:
            continue

        import re as _re
        start_raw = _re.sub(r'\.\d+Z$', 'Z', ev.get("start_at", ""))
        end_raw = _re.sub(r'\.\d+Z$', 'Z', ev.get("end_at", ""))
        geo = ev.get("geo_address_info") or {}
        location_name = ev.get("geo_address_json", {}).get("description", "") if isinstance(ev.get("geo_address_json"), dict) else ""

        cover = ev.get("cover_url") or ev.get("cover_image_url")

        events.append(
            Event(
                id=make_event_id("luma", title, start_raw),
                source=EventSource.LUMA,
                title=title,
                description=(ev.get("description") or "")[:500],
                url=f"https://lu.ma/{ev['api_id']}" if ev.get("api_id") else ev.get("url", ""),
                start_time=parse_event_dt(start_raw),
                end_time=parse_event_dt(end_raw),
                location_name=location_name or geo.get("city_state", ""),
                location_address=geo.get("full_address", ""),
                is_online=ev.get("meeting_url") is not None and not ev.get("geo_latitude"),
                image_url=cover,
            )
        )
    return events


# ── Scrape fallback ───────────────────────────────────────────────────────────

async def _fetch_via_scrape(settings: Settings) -> list[Event]:
    url = "https://lu.ma/discover/boston"
    headers = {"User-Agent": USER_AGENT}

    async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True) as client:
        resp = await client.get(url, headers=headers)
        resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "lxml")
    events: list[Event] = []

    cards = soup.select(
        "div[class*='event-card'], "
        "a[class*='event-link'], "
        "div[class*='content-card']"
    )
    if not cards:
        cards = soup.select("a[href*='/']")
        cards = [c for c in cards if c.find(["h2", "h3"])]

    for card in cards:
        link_tag = card.find("a", href=True) if card.name != "a" else card
        event_url = ""
        if link_tag and link_tag.get("href"):
            href = link_tag["href"]
            event_url = href if href.startswith("http") else f"https://lu.ma{href}"

        title_tag = card.find(["h2", "h3", "span"]) or card.find(class_=lambda c: c and "title" in str(c).lower())
        title = title_tag.get_text(strip=True) if title_tag else ""
        if not title or len(title) < 3:
            continue

        time_tag = card.find("time") or card.find(class_=lambda c: c and "date" in str(c).lower())
        date_str = ""
        if time_tag:
            date_str = time_tag.get("datetime", "") or time_tag.get_text(strip=True)
        start_time = parse_event_dt(date_str)

        location_tag = card.find(class_=lambda c: c and "location" in str(c).lower())
        location = location_tag.get_text(strip=True) if location_tag else ""

        img_tag = card.find("img", src=True)
        image_url = img_tag["src"] if img_tag else None

        events.append(
            Event(
                id=make_event_id("luma", title, date_str),
                source=EventSource.LUMA,
                title=title,
                url=event_url,
                start_time=start_time,
                location_name=location,
                image_url=image_url,
            )
        )

    return events


# ── Public entry point ────────────────────────────────────────────────────────

async def fetch_luma(settings: Settings) -> list[Event]:
    """Fetch events from Luma (API first, scrape fallback)."""
    try:
        logger.info("Fetching Luma events via API")
        return await _fetch_via_api(settings)
    except Exception:
        logger.warning("Luma API failed, falling back to scrape", exc_info=True)
    try:
        return await _fetch_via_scrape(settings)
    except Exception:
        logger.exception("Luma scrape fallback also failed")
        return []
