"""Spotify activity ingest via spotipy."""

from __future__ import annotations

import logging
from datetime import datetime

from recom.config import Settings
from recom.models import ActivityItem

logger = logging.getLogger(__name__)

SCOPES = "user-read-recently-played user-top-read user-library-read"


def _build_client(settings: Settings):
    """Create an authenticated Spotify client, or None if credentials are missing."""
    try:
        import spotipy
        from spotipy.oauth2 import SpotifyOAuth
    except ImportError:
        logger.warning("spotipy is not installed; skipping Spotify ingest")
        return None

    if not settings.spotify_client_id or not settings.spotify_client_secret:
        logger.warning(
            "Spotify credentials not configured (RECOM_SPOTIFY_CLIENT_ID / "
            "RECOM_SPOTIFY_CLIENT_SECRET). Skipping Spotify ingest."
        )
        return None

    try:
        auth_manager = SpotifyOAuth(
            client_id=settings.spotify_client_id,
            client_secret=settings.spotify_client_secret,
            redirect_uri=settings.spotify_redirect_uri,
            scope=SCOPES,
            cache_path=settings.spotify_token_file,
            open_browser=False,
        )
        token_info = auth_manager.cache_handler.get_cached_token()
        if not token_info:
            logger.warning(
                "No cached Spotify token found at %s. Run scripts/auth_spotify.py first.",
                settings.spotify_token_file,
            )
            return None

        return spotipy.Spotify(auth_manager=auth_manager)
    except Exception:
        logger.exception("Failed to create Spotify client")
        return None


def _fetch_top_artists(sp) -> list[ActivityItem]:
    """Fetch the user's top artists (medium term)."""
    items: list[ActivityItem] = []
    try:
        results = sp.current_user_top_artists(limit=50, time_range="medium_term")
        for artist in results.get("items", []):
            genres = artist.get("genres", [])
            items.append(
                ActivityItem(
                    source="spotify",
                    title=artist.get("name", ""),
                    category="top_artist",
                    description=", ".join(genres[:5]) if genres else None,
                    url=(artist.get("external_urls") or {}).get("spotify"),
                )
            )
    except Exception:
        logger.exception("Failed to fetch Spotify top artists")
    logger.info("Fetched %d Spotify top artists", len(items))
    return items


def _fetch_top_tracks(sp) -> list[ActivityItem]:
    """Fetch the user's top tracks (medium term)."""
    items: list[ActivityItem] = []
    try:
        results = sp.current_user_top_tracks(limit=50, time_range="medium_term")
        for track in results.get("items", []):
            artists = ", ".join(a.get("name", "") for a in track.get("artists", []))
            items.append(
                ActivityItem(
                    source="spotify",
                    title=track.get("name", ""),
                    category="top_track",
                    description=f"by {artists}" if artists else None,
                    url=(track.get("external_urls") or {}).get("spotify"),
                )
            )
    except Exception:
        logger.exception("Failed to fetch Spotify top tracks")
    logger.info("Fetched %d Spotify top tracks", len(items))
    return items


def _fetch_recently_played(sp) -> list[ActivityItem]:
    """Fetch recently played tracks."""
    items: list[ActivityItem] = []
    try:
        results = sp.current_user_recently_played(limit=50)
        for entry in results.get("items", []):
            track = entry.get("track", {})
            artists = ", ".join(a.get("name", "") for a in track.get("artists", []))
            played_at = None
            if raw := entry.get("played_at"):
                try:
                    played_at = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                except (ValueError, TypeError):
                    pass
            items.append(
                ActivityItem(
                    source="spotify",
                    title=track.get("name", ""),
                    category="recently_played",
                    description=f"by {artists}" if artists else None,
                    timestamp=played_at,
                    url=(track.get("external_urls") or {}).get("spotify"),
                )
            )
    except Exception:
        logger.exception("Failed to fetch Spotify recently played")
    logger.info("Fetched %d Spotify recently played tracks", len(items))
    return items


def get_spotify_activity(settings: Settings) -> list[ActivityItem]:
    """Return Spotify activity items.

    Pulls top artists, top tracks, and recently played.
    Returns an empty list if credentials are missing or an error occurs.
    """
    sp = _build_client(settings)
    if sp is None:
        return []

    items: list[ActivityItem] = []
    items.extend(_fetch_top_artists(sp))
    items.extend(_fetch_top_tracks(sp))
    items.extend(_fetch_recently_played(sp))

    logger.info("Total Spotify activity items: %d", len(items))
    return items
