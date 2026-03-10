"""Batch geocoder for event venues using Nominatim (OpenStreetMap, no key needed).

Caches results in-memory per pipeline run to avoid redundant requests.
Rate-limited to 1 req/sec as required by Nominatim ToS.
"""
from __future__ import annotations

import logging
import time
from functools import lru_cache

import httpx

from recom.models import Event

logger = logging.getLogger(__name__)

# Cambridge, MA as fallback center
HOME_LAT = 42.3736
HOME_LON = -71.1097

_NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
_USER_AGENT = "recom-event-recommender/1.0"

# In-memory cache: query string → (lat, lon) or None
_geocache: dict[str, tuple[float, float] | None] = {}
_last_request_time: float = 0.0


def _geocode_query(query: str) -> tuple[float, float] | None:
    """Hit Nominatim with 1 req/sec rate limit."""
    global _last_request_time

    if query in _geocache:
        return _geocache[query]

    # Rate limit
    now = time.monotonic()
    wait = 1.0 - (now - _last_request_time)
    if wait > 0:
        time.sleep(wait)
    _last_request_time = time.monotonic()

    try:
        resp = httpx.get(
            _NOMINATIM_URL,
            params={"q": query, "format": "json", "limit": 1, "addressdetails": 0},
            headers={"User-Agent": _USER_AGENT},
            timeout=8.0,
        )
        resp.raise_for_status()
        results = resp.json()
        if results:
            lat = float(results[0]["lat"])
            lon = float(results[0]["lon"])
            _geocache[query] = (lat, lon)
            return lat, lon
    except Exception as exc:
        logger.debug("Geocode failed for %r: %s", query, exc)

    _geocache[query] = None
    return None


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Return distance in km between two lat/lon points."""
    import math
    R = 6371
    dLat = math.radians(lat2 - lat1)
    dLon = math.radians(lon2 - lon1)
    a = math.sin(dLat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dLon / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def geocode_events(events: list[Event], home_lat: float = HOME_LAT, home_lon: float = HOME_LON) -> list[Event]:
    """Enrich events with lat/lon by geocoding venue names.

    Only geocodes events missing coordinates. Skips online events.
    Returns the same list with lat/lon fields populated where possible.
    """
    needs_geocode = [e for e in events if e.lat is None and e.lon is None and not e.is_online]
    if not needs_geocode:
        return events

    logger.info("Geocoding %d events (may take ~%ds at 1 req/s)...", len(needs_geocode), len(needs_geocode))

    # Deduplicate queries
    queries: dict[str, list[Event]] = {}
    for e in needs_geocode:
        # Build best query: "Venue Name, City" or just address
        parts = [p for p in [e.location_name, e.location_address] if p and p.strip()]
        if not parts:
            continue
        query = ", ".join(parts)
        # Append "Boston MA" as context if no city hint
        if not any(kw in query.lower() for kw in ["boston", "cambridge", "somerville", "brookline", "ma", "massachusetts"]):
            query += ", Boston MA"
        queries.setdefault(query, []).append(e)

    for query, evts in queries.items():
        coords = _geocode_query(query)
        if coords:
            lat, lon = coords
            for ev in evts:
                ev.lat = lat
                ev.lon = lon

    geocoded = sum(1 for e in needs_geocode if e.lat is not None)
    logger.info("Geocoded %d/%d events", geocoded, len(needs_geocode))
    return events
