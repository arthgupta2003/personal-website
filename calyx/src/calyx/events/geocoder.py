"""Batch geocoder for event venues using Nominatim (OpenStreetMap, no key needed).

Caches results persistently in SQLite so venues aren't re-geocoded across runs.
Rate-limited to 1 req/sec as required by Nominatim ToS.
"""
from __future__ import annotations

import logging
import sqlite3
import time
from pathlib import Path

import httpx

from calyx.models import Event

logger = logging.getLogger(__name__)

# Cambridge, MA as fallback center
HOME_LAT = 42.3736
HOME_LON = -71.1097

_NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
_USER_AGENT = "recom-event-recommender/1.0"

_last_request_time: float = 0.0

# Persistent geocache in SQLite (next to calyx.db)
_geocache_db: sqlite3.Connection | None = None


def _get_geocache_db() -> sqlite3.Connection:
    """Get or create the geocache SQLite connection."""
    global _geocache_db
    if _geocache_db is None:
        cache_path = Path("state/geocache.db")
        cache_path.parent.mkdir(exist_ok=True)
        _geocache_db = sqlite3.connect(str(cache_path))
        _geocache_db.execute("""
            CREATE TABLE IF NOT EXISTS geocache (
                query TEXT PRIMARY KEY,
                lat REAL,
                lon REAL,
                cached_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        _geocache_db.commit()
    return _geocache_db


def _geocache_lookup(query: str) -> tuple[float, float] | None:
    """Check persistent cache for a geocoded query."""
    db = _get_geocache_db()
    row = db.execute("SELECT lat, lon FROM geocache WHERE query = ?", (query,)).fetchone()
    if row and row[0] is not None:
        return (row[0], row[1])
    return None


def _geocache_store(query: str, lat: float | None, lon: float | None):
    """Store a geocode result in the persistent cache."""
    db = _get_geocache_db()
    db.execute(
        "INSERT OR REPLACE INTO geocache (query, lat, lon) VALUES (?, ?, ?)",
        (query, lat, lon),
    )
    db.commit()


def _geocode_query(query: str) -> tuple[float, float] | None:
    """Hit Nominatim with 1 req/sec rate limit, checking persistent cache first."""
    global _last_request_time

    # Check persistent cache first
    cached = _geocache_lookup(query)
    if cached is not None:
        return cached

    # Check if we already know this query has no result
    db = _get_geocache_db()
    row = db.execute("SELECT lat FROM geocache WHERE query = ?", (query,)).fetchone()
    if row is not None:  # exists in cache but lat is None
        return None

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
            _geocache_store(query, lat, lon)
            return lat, lon
    except Exception as exc:
        logger.debug("Geocode failed for %r: %s", query, exc)

    _geocache_store(query, None, None)
    return None


def geocode_events(events: list[Event], home_lat: float = HOME_LAT, home_lon: float = HOME_LON) -> list[Event]:
    """Enrich events with lat/lon by geocoding venue names.

    Only geocodes events missing coordinates. Skips online events.
    Returns the same list with lat/lon fields populated where possible.
    """
    needs_geocode = [e for e in events if e.lat is None and e.lon is None and not e.is_online]
    if not needs_geocode:
        return events

    # Deduplicate queries
    queries: dict[str, list[Event]] = {}
    for e in needs_geocode:
        parts = [p for p in [e.location_name, e.location_address] if p and p.strip()]
        if not parts:
            continue
        query = ", ".join(parts)
        if not any(kw in query.lower() for kw in ["boston", "cambridge", "somerville", "brookline", "ma", "massachusetts"]):
            query += ", Boston MA"
        queries.setdefault(query, []).append(e)

    # Check how many are already cached
    cached_count = sum(1 for q in queries if _geocache_lookup(q) is not None)
    uncached = len(queries) - cached_count
    logger.info(
        "Geocoding %d unique venues (%d cached, %d need API calls ~%ds)...",
        len(queries), cached_count, uncached, uncached,
    )

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
