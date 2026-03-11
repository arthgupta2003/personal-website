"""Outdoor & nature day trip recommendations for the Boston area.

These are "always available" experiences with no fixed start_time.
Sources: DCR state parks, AMC, Mass Audubon, Trustees of Reservations.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime

import httpx
from bs4 import BeautifulSoup

from recom.config import Settings
from recom.events.common import make_event_id
from recom.models import Event, EventSource

logger = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
TIMEOUT = 25.0




# ── Curated seed spots (always show, no scraping needed) ─────────────────────

_SEED_SPOTS = [
    {
        "title": "Middlesex Fells Reservation — Hiking & Trails",
        "description": "4,000-acre reservation just north of Boston with 100+ miles of trails. Great for hiking, mountain biking, and swimming at Spot Pond. Multiple difficulty levels.",
        "location_name": "Middlesex Fells Reservation",
        "location_address": "4 Woodland Rd, Stoneham, MA 01880",
        "url": "https://www.mass.gov/locations/middlesex-fells-reservation",
        "category": "outdoor",
        "lat": 42.4565, "lon": -71.1017,
    },
    {
        "title": "Blue Hills Reservation — Hiking & Views",
        "description": "7,000-acre reservation with Great Blue Hill summit (635ft), panoramic views of Boston skyline. 125+ miles of trails, ski area in winter.",
        "location_name": "Blue Hills Reservation",
        "location_address": "695 Hillside St, Milton, MA 02186",
        "url": "https://www.mass.gov/locations/blue-hills-reservation",
        "category": "outdoor",
        "lat": 42.2176, "lon": -71.1143,
    },
    {
        "title": "Walden Pond State Reservation",
        "description": "The historic pond where Thoreau lived. Swimming, hiking the 1.7-mile perimeter trail, kayaking. Beautiful in every season. 30 min from Cambridge.",
        "location_name": "Walden Pond",
        "location_address": "915 Walden St, Concord, MA 01742",
        "url": "https://www.mass.gov/locations/walden-pond-state-reservation",
        "category": "outdoor",
        "lat": 42.4381, "lon": -71.3355,
    },
    {
        "title": "Boston Harbor Islands — Day Trip by Ferry",
        "description": "Island archipelago in Boston Harbor. Ferry from Long Wharf. Spectacle Island has beach and hilltop views. George's Island has a Civil War fort. Kayaking, camping available.",
        "location_name": "Boston Harbor Islands",
        "location_address": "Long Wharf, Boston, MA 02110",
        "url": "https://www.bostonharborislands.org",
        "category": "outdoor",
        "lat": 42.3123, "lon": -70.9548,
    },
    {
        "title": "Minute Man National Historical Park — Bike/Hike",
        "description": "Follow the Battle Road Trail through Lexington and Concord — 5.5-mile mostly flat paved path through historic Revolutionary War landscapes. Great for cycling.",
        "location_name": "Minute Man NHP Visitor Center",
        "location_address": "250 North Great Rd, Lincoln, MA 01773",
        "url": "https://www.nps.gov/mima",
        "category": "outdoor",
        "lat": 42.4215, "lon": -71.3270,
    },
    {
        "title": "Breakheart Reservation — Loop Trails",
        "description": "640-acre wooded reservation in Saugus/Wakefield. Two ponds for swimming, 6 miles of trails, rocky ledges with views. Less crowded than Fells.",
        "location_name": "Breakheart Reservation",
        "location_address": "177 Forest St, Saugus, MA 01906",
        "url": "https://www.mass.gov/locations/breakheart-reservation",
        "category": "outdoor",
        "lat": 42.4962, "lon": -71.0237,
    },
    {
        "title": "Lynn Woods — Urban Forest Trails",
        "description": "2,200-acre forest — one of the largest municipal parks in the US. Dungeon Rock cave, glacial boulders, 30+ miles of trails. Free, open year-round.",
        "location_name": "Lynn Woods Reservation",
        "location_address": "Penny Brook Rd, Lynn, MA 01904",
        "url": "https://www.lynnwoods.org",
        "category": "outdoor",
        "lat": 42.5050, "lon": -70.9851,
    },
    {
        "title": "World's End — Coastal Walk (Hingham)",
        "description": "Trustees of Reservations property. 251-acre peninsula with sweeping views of Boston skyline and Weir River. 4+ miles of carriage paths, marsh, and seaside trails. $10 entry.",
        "location_name": "World's End",
        "location_address": "250 Martin's Ln, Hingham, MA 02043",
        "url": "https://www.thetrustees.org/place/worlds-end/",
        "category": "outdoor",
        "lat": 42.2472, "lon": -70.8795,
    },
    {
        "title": "Halibut Point State Park — Rockport Coastal Hike",
        "description": "Dramatic rocky coastline on Cape Ann. Old granite quarry, tide pools, ocean views. Short hike (~1 mile) with optional tidal exploration. 1.5hr from Cambridge.",
        "location_name": "Halibut Point State Park",
        "location_address": "Gott Ave, Rockport, MA 01966",
        "url": "https://www.mass.gov/locations/halibut-point-state-park",
        "category": "outdoor",
        "lat": 42.6853, "lon": -70.6303,
    },
    {
        "title": "Mt. Monadnock — Day Hike (NH)",
        "description": "Most climbed mountain in North America. 3,165ft summit with views across 6 states on clear days. White Dot Trail is classic 3.8mi round trip. 1.5hr from Cambridge.",
        "location_name": "Monadnock State Park",
        "location_address": "116 Poole Rd, Jaffrey, NH 03452",
        "url": "https://www.nhstateparks.org/visit/state-parks/monadnock-state-park",
        "category": "outdoor",
        "lat": 42.8620, "lon": -72.1076,
    },
    {
        "title": "Charles River Canoe & Kayak — Paddling",
        "description": "Rent canoes, kayaks, or SUPs on the Charles River. Multiple launch sites in Cambridge, Newton, and Waltham. No experience needed. Open April-October.",
        "location_name": "Charles River Canoe & Kayak",
        "location_address": "2401 Commonwealth Ave, Newton, MA 02466",
        "url": "https://ski-paddle.com",
        "category": "outdoor",
        "lat": 42.3482, "lon": -71.1891,
    },
    {
        "title": "Arnold Arboretum — Walking & Picnic",
        "description": "281-acre living museum of trees and shrubs in Jamaica Plain, operated by Harvard. Free admission. Spectacular lilac Sunday in May. Beautiful year-round.",
        "location_name": "Arnold Arboretum",
        "location_address": "125 Arborway, Boston, MA 02130",
        "url": "https://arboretum.harvard.edu",
        "category": "outdoor",
        "lat": 42.3025, "lon": -71.1262,
    },
]


async def _fetch_amc_events(settings: Settings) -> list[Event]:
    """Fetch AMC organized hikes and outdoor activities."""
    url = "https://www.outdoors.org/activities-events/outdoor-activities/"
    events: list[Event] = []

    async with httpx.AsyncClient(headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT, follow_redirects=True) as client:
        try:
            resp = await client.get(url)
            resp.raise_for_status()
        except Exception as exc:
            logger.warning("AMC fetch failed: %s", exc)
            return []

    soup = BeautifulSoup(resp.text, "html.parser")
    for card in soup.select(".activity-card, .event-card, article, [class*='activity'], [class*='event']")[:20]:
        title_el = card.select_one("h2, h3, h4, .title")
        if not title_el:
            continue
        title = title_el.get_text(strip=True)
        if not title or len(title) < 5:
            continue

        link_el = card.select_one("a[href]")
        event_url = ""
        if link_el:
            href = link_el.get("href", "")
            event_url = href if href.startswith("http") else f"https://www.outdoors.org{href}"

        date_el = card.select_one("time, .date, [class*='date']")
        start_time = None
        if date_el:
            from recom.models import parse_event_dt
            start_time = parse_event_dt(date_el.get("datetime") or date_el.get_text(strip=True))

        desc_el = card.select_one("p, .description, .summary")
        description = desc_el.get_text(strip=True)[:300] if desc_el else "AMC organized outdoor activity in the Boston area."

        events.append(Event(
            id=make_event_id("amc", title),
            source=EventSource.NEWSLETTER,  # reuse as generic
            title=f"AMC: {title}",
            description=description,
            url=event_url,
            start_time=start_time,
            location_name="Greater Boston Area",
            location_address="Boston, MA",
            organizer="Appalachian Mountain Club",
            category="outdoor",
        ))

    logger.info("AMC returned %d outdoor events", len(events))
    return events


async def fetch_outdoor_events(settings: Settings) -> list[Event]:
    """Return curated outdoor spots + AMC organized events."""
    events: list[Event] = []

    # Add curated seed spots (always available, no start_time)
    for spot in _SEED_SPOTS:
        events.append(Event(
            id=make_event_id("seed", spot["title"]),
            source=EventSource.NEWSLETTER,
            title=spot["title"],
            description=spot["description"],
            url=spot["url"],
            start_time=None,  # always available
            location_name=spot["location_name"],
            location_address=spot["location_address"],
            organizer="DCR / Trustees / NPS",
            category="outdoor",
            lat=spot.get("lat"),
            lon=spot.get("lon"),
        ))

    # AMC organized events
    try:
        amc_events = await _fetch_amc_events(settings)
        events.extend(amc_events)
    except Exception as exc:
        logger.warning("AMC events failed: %s", exc)

    logger.info("Outdoor source returned %d total entries", len(events))
    return events
