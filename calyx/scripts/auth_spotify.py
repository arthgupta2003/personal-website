#!/usr/bin/env python3
"""One-time Spotify OAuth flow.

Run this script to authenticate and cache the Spotify token:
    python scripts/auth_spotify.py

Prerequisites:
    - Set RECOM_SPOTIFY_CLIENT_ID and RECOM_SPOTIFY_CLIENT_SECRET in .env
"""

import logging
import sys

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

SCOPES = "user-read-recently-played user-top-read user-library-read"


def main() -> None:
    try:
        from spotipy.oauth2 import SpotifyOAuth
    except ImportError:
        logger.error("spotipy is not installed. Run: pip install spotipy")
        sys.exit(1)

    try:
        from calyx.config import Settings
    except ImportError:
        logger.error("recom package not found. Run: pip install -e .")
        sys.exit(1)

    settings = Settings()

    if not settings.spotify_client_id or not settings.spotify_client_secret:
        logger.error(
            "Spotify credentials not set. Add RECOM_SPOTIFY_CLIENT_ID and "
            "RECOM_SPOTIFY_CLIENT_SECRET to your .env file."
        )
        sys.exit(1)

    auth_manager = SpotifyOAuth(
        client_id=settings.spotify_client_id,
        client_secret=settings.spotify_client_secret,
        redirect_uri=settings.spotify_redirect_uri,
        scope=SCOPES,
        cache_path=settings.spotify_token_file,
    )

    logger.info("Opening browser for Spotify authorization...")
    token_info = auth_manager.get_access_token(as_dict=True)

    if token_info:
        logger.info("Spotify credentials cached at %s", settings.spotify_token_file)
    else:
        logger.error("Failed to obtain Spotify access token")
        sys.exit(1)


if __name__ == "__main__":
    main()
