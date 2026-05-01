"""TimeOut Boston event scraper — no API key required."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone

import httpx
from bs4 import BeautifulSoup

from calyx.config import Settings
from calyx.events.common import make_event_id
from calyx.models import Event, EventSource

logger = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
TIMEOUT = 30.0



def _parse_timeout_date(date_str: str) -> datetime | None:
    """Parse TimeOut date strings like 'Sat Mar 15 2026', 'Mar 15', etc."""
    if not date_str:
        return None
    date_str = date_str.strip()
    year = datetime.now().year
    formats = [
        "%a %b %d %Y",
        "%B %d, %Y",
        "%b %d, %Y",
        "%b %d %Y",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(date_str, fmt)
        except ValueError:
            pass
    # Try appending current year
    for fmt in ["%b %d", "%B %d"]:
        try:
            return datetime.strptime(f"{date_str} {year}", f"{fmt} %Y")
        except ValueError:
            pass
    return None


async def fetch_timeout_boston(settings: Settings) -> list[Event]:
    """Scrape TimeOut Boston events listing."""
    headers = {"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"}
    now = datetime.now(timezone.utc)
    cutoff = now + timedelta(days=14)
    events: list[Event] = []
    seen_titles: set[str] = set()

    urls = [
        "https://www.timeout.com/boston/things-to-do/boston-events-calendar",
        "https://www.timeout.com/boston/music/concerts-in-boston",
        "https://www.timeout.com/boston/comedy/best-comedy-shows-in-boston",
    ]

    async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True) as client:
        for url in urls:
            try:
                resp = await client.get(url, headers=headers)
                if resp.status_code != 200:
                    logger.debug("TimeOut URL %s returned %d", url, resp.status_code)
                    continue
                soup = BeautifulSoup(resp.text, "html.parser")

                # TimeOut uses article cards — look for JSON-LD first
                for script in soup.find_all("script", type="application/ld+json"):
                    try:
                        import json
                        data = json.loads(script.string or "")
                        items = data if isinstance(data, list) else [data]
                        for item in items:
                            if item.get("@type") not in ("Event", "MusicEvent", "TheaterEvent", "ComedyEvent"):
                                continue
                            title = item.get("name", "")
                            if not title or title.lower() in seen_titles:
                                continue
                            seen_titles.add(title.lower())

                            start_raw = item.get("startDate", "")
                            start_time = _parse_timeout_date(start_raw)
                            location = item.get("location") or {}
                            venue_name = ""
                            venue_addr = ""
                            if isinstance(location, dict):
                                venue_name = location.get("name", "")
                                addr = location.get("address") or {}
                                if isinstance(addr, dict):
                                    venue_addr = addr.get("streetAddress", "")
                                elif isinstance(addr, str):
                                    venue_addr = addr

                            evt_url = item.get("url", "") or item.get("@id", "")
                            description = item.get("description", "")
                            price = ""
                            offers = item.get("offers")
                            if isinstance(offers, dict):
                                price = str(offers.get("price", "")) or offers.get("priceCurrency", "")
                            elif isinstance(offers, list) and offers:
                                p = offers[0].get("price", "")
                                price = str(p) if p else ""

                            events.append(Event(
                                id=make_event_id("timeout", title, start_raw),
                                source=EventSource.TIMEOUT_BOSTON,
                                title=title,
                                description=description[:500],
                                url=evt_url,
                                start_time=start_time,
                                location_name=venue_name,
                                location_address=venue_addr,
                                price=price or None,
                            ))
                    except Exception:
                        pass

                # Fallback: parse article cards
                if not events:
                    for article in soup.find_all(["article", "li"], class_=re.compile(r"card|tile|item", re.I)):
                        title_el = article.find(["h2", "h3", "h4", "a"], class_=re.compile(r"title|heading|name", re.I))
                        if not title_el:
                            title_el = article.find("a")
                        if not title_el:
                            continue
                        title = title_el.get_text(strip=True)
                        if not title or title.lower() in seen_titles:
                            continue
                        seen_titles.add(title.lower())

                        link_el = article.find("a", href=True)
                        evt_url = link_el["href"] if link_el else ""
                        if evt_url and evt_url.startswith("/"):
                            evt_url = "https://www.timeout.com" + evt_url

                        date_el = article.find(class_=re.compile(r"date|time|when", re.I))
                        date_str = date_el.get_text(strip=True) if date_el else ""
                        start_time = _parse_timeout_date(date_str)

                        venue_el = article.find(class_=re.compile(r"venue|location|place", re.I))
                        venue = venue_el.get_text(strip=True) if venue_el else ""

                        events.append(Event(
                            id=make_event_id("timeout", title, date_str),
                            source=EventSource.TIMEOUT_BOSTON,
                            title=title,
                            url=evt_url,
                            start_time=start_time,
                            location_name=venue,
                        ))

            except Exception:
                logger.exception("TimeOut Boston scrape failed for %s", url)

    # Filter to upcoming events
    result = []
    for e in events:
        if e.start_time is None:
            result.append(e)
        else:
            st = e.start_time
            if st.tzinfo is None:
                st = st.replace(tzinfo=timezone.utc)
            if now <= st <= cutoff:
                result.append(e)

    logger.info("TimeOut Boston returned %d events", len(result))
    return result
