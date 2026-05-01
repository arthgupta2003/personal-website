"""YouTube activity ingest via Data Portability API and YouTube Data API v3."""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime
from pathlib import Path

from calyx.config import Settings
from calyx.models import ActivityItem

logger = logging.getLogger(__name__)

YT_SCOPES = ["https://www.googleapis.com/auth/youtube.readonly"]
DP_SCOPES = ["https://www.googleapis.com/auth/dataportability.myactivity.youtube"]

_POLL_INTERVAL_SECONDS = 10
_POLL_MAX_ATTEMPTS = 60  # ~10 minutes


def _load_credentials(settings: Settings):
    """Load saved OAuth2 credentials from the token file."""
    from google.oauth2.credentials import Credentials

    token_path = Path(settings.youtube_token_file)
    if not token_path.exists():
        logger.warning(
            "YouTube token file not found at %s. Run scripts/auth_youtube.py first.",
            token_path,
        )
        return None

    try:
        creds = Credentials.from_authorized_user_file(str(token_path), YT_SCOPES)
    except Exception:
        logger.exception("Failed to load YouTube credentials")
        return None

    if creds.expired and creds.refresh_token:
        try:
            from google.auth.transport.requests import Request

            creds.refresh(Request())
            token_path.write_text(creds.to_json())
        except Exception:
            logger.exception("Failed to refresh YouTube credentials")
            return None

    return creds


# ---------------------------------------------------------------------------
# Data Portability API (primary)
# ---------------------------------------------------------------------------


def _fetch_via_data_portability(creds) -> list[ActivityItem]:
    """Export watch history using Google Data Portability API."""
    try:
        from googleapiclient.discovery import build
    except ImportError:
        logger.warning("google-api-python-client not installed; skipping Data Portability")
        return []

    items: list[ActivityItem] = []

    try:
        service = build("dataportability", "v1", credentials=creds)

        # Initiate the archive export
        logger.info("Initiating Data Portability archive for myactivity.youtube")
        initiate_resp = (
            service.portabilityArchive()
            .initiate(body={"resources": ["myactivity.youtube"]})
            .execute()
        )
        job_id = initiate_resp.get("archiveJobId")
        if not job_id:
            logger.error("No archiveJobId returned from portabilityArchive.initiate")
            return []

        # Poll until COMPLETE
        state = None
        for attempt in range(_POLL_MAX_ATTEMPTS):
            state_resp = (
                service.archiveJobs()
                .getPortabilityArchiveState(name=f"archiveJobs/{job_id}")
                .execute()
            )
            state = state_resp.get("state")
            logger.debug("Archive job %s state: %s (attempt %d)", job_id, state, attempt + 1)

            if state == "COMPLETE":
                urls = state_resp.get("urls", [])
                items = _parse_portability_archive(urls)
                break
            elif state in ("FAILED", "CANCELLED"):
                logger.error("Archive job %s ended with state %s", job_id, state)
                break
            else:
                time.sleep(_POLL_INTERVAL_SECONDS)
        else:
            logger.error("Archive job %s did not complete within polling window", job_id)

    except Exception:
        logger.exception("Data Portability API call failed")

    return items


def _parse_portability_archive(urls: list[str]) -> list[ActivityItem]:
    """Download and parse watch history JSON from signed GCS URLs."""
    import urllib.request

    items: list[ActivityItem] = []

    for url in urls:
        try:
            with urllib.request.urlopen(url) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception:
            logger.exception("Failed to download/parse archive URL: %s", url)
            continue

        if not isinstance(data, list):
            data = [data]

        for entry in data:
            try:
                title = entry.get("title", "").removeprefix("Watched ")
                title_url = entry.get("titleUrl", "")
                timestamp = None
                if raw_time := entry.get("time"):
                    try:
                        timestamp = datetime.fromisoformat(raw_time.replace("Z", "+00:00"))
                    except (ValueError, TypeError):
                        pass

                channel = None
                subtitles = entry.get("subtitles", [])
                if subtitles and isinstance(subtitles, list):
                    channel = subtitles[0].get("name")

                items.append(
                    ActivityItem(
                        source="youtube",
                        title=title,
                        category="watch_history",
                        description=f"Channel: {channel}" if channel else None,
                        timestamp=timestamp,
                        url=title_url or None,
                    )
                )
            except Exception:
                logger.debug("Skipping malformed watch-history entry: %s", entry)

    logger.info("Parsed %d items from Data Portability archive", len(items))
    return items


# ---------------------------------------------------------------------------
# YouTube Data API v3 (supplement / fallback)
# ---------------------------------------------------------------------------


def _fetch_via_youtube_api(creds) -> list[ActivityItem]:
    """Fetch subscriptions and liked videos via YouTube Data API v3."""
    try:
        from googleapiclient.discovery import build
    except ImportError:
        logger.warning("google-api-python-client not installed; skipping YouTube Data API")
        return []

    items: list[ActivityItem] = []

    try:
        yt = build("youtube", "v3", credentials=creds)

        # Subscriptions
        items.extend(_fetch_subscriptions(yt))

        # Liked videos
        items.extend(_fetch_liked_videos(yt))

    except Exception:
        logger.exception("YouTube Data API calls failed")

    return items


def _fetch_subscriptions(yt) -> list[ActivityItem]:
    """Page through subscriptions.list(mine=True)."""
    items: list[ActivityItem] = []
    page_token = None

    try:
        while True:
            resp = (
                yt.subscriptions()
                .list(part="snippet", mine=True, maxResults=50, pageToken=page_token)
                .execute()
            )
            for sub in resp.get("items", []):
                snippet = sub.get("snippet", {})
                channel_title = snippet.get("title", "Unknown Channel")
                channel_id = snippet.get("resourceId", {}).get("channelId", "")
                items.append(
                    ActivityItem(
                        source="youtube",
                        title=channel_title,
                        category="subscription",
                        description=snippet.get("description", "")[:200] or None,
                        url=f"https://www.youtube.com/channel/{channel_id}" if channel_id else None,
                    )
                )
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
    except Exception:
        logger.exception("Failed to fetch YouTube subscriptions")

    logger.info("Fetched %d YouTube subscriptions", len(items))
    return items


def _fetch_liked_videos(yt) -> list[ActivityItem]:
    """Page through playlistItems.list for the Liked Videos playlist."""
    items: list[ActivityItem] = []
    page_token = None

    try:
        while True:
            resp = (
                yt.playlistItems()
                .list(part="snippet", playlistId="LL", maxResults=50, pageToken=page_token)
                .execute()
            )
            for item in resp.get("items", []):
                snippet = item.get("snippet", {})
                video_id = snippet.get("resourceId", {}).get("videoId", "")
                published = None
                if raw := snippet.get("publishedAt"):
                    try:
                        published = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                    except (ValueError, TypeError):
                        pass
                items.append(
                    ActivityItem(
                        source="youtube",
                        title=snippet.get("title", ""),
                        category="liked_video",
                        description=snippet.get("description", "")[:200] or None,
                        timestamp=published,
                        url=f"https://www.youtube.com/watch?v={video_id}" if video_id else None,
                    )
                )
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
    except Exception:
        logger.exception("Failed to fetch YouTube liked videos")

    logger.info("Fetched %d YouTube liked videos", len(items))
    return items


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def get_youtube_activity(settings: Settings) -> list[ActivityItem]:
    """Return YouTube activity items.

    Tries Data Portability API first, then supplements with YouTube Data API v3.
    Returns an empty list if no credentials are available.
    """
    creds = _load_credentials(settings)
    items: list[ActivityItem] = []

    # Primary: Data Portability API (separate token file)
    history_token_path = Path(settings.youtube_token_file).parent / "youtube_history_token.json"
    if history_token_path.exists():
        try:
            from google.oauth2.credentials import Credentials
            dp_creds = Credentials.from_authorized_user_file(str(history_token_path), DP_SCOPES)
            portability_items = _fetch_via_data_portability(dp_creds)
            items.extend(portability_items)
        except Exception:
            logger.warning("Data Portability API failed", exc_info=True)
    else:
        logger.info("No YouTube history token — skipping watch history. Run scripts/auth_youtube_history.py")

    # Supplement: YouTube Data API v3
    if creds is not None:
        api_items = _fetch_via_youtube_api(creds)
        items.extend(api_items)
    else:
        logger.warning("No YouTube API token — skipping subscriptions/likes")

    logger.info("Total YouTube activity items: %d", len(items))
    return items
