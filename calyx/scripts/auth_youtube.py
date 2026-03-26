#!/usr/bin/env python3
"""One-time OAuth2 flow for YouTube Data API (subscriptions + liked videos)."""

import logging
import sys
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# YouTube API only — Data Portability requires a separate flow
SCOPES = ["https://www.googleapis.com/auth/youtube.readonly"]
CLIENT_SECRETS_FILE = Path("state/tokens/client_secret.json")
TOKEN_FILE = Path("state/tokens/youtube_token.json")


def main() -> None:
    if not CLIENT_SECRETS_FILE.exists():
        logger.error("Client secrets not found at %s", CLIENT_SECRETS_FILE)
        sys.exit(1)

    flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRETS_FILE), scopes=SCOPES)
    creds = flow.run_local_server(port=8889)

    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(creds.to_json())
    logger.info("YouTube token saved to %s", TOKEN_FILE)


if __name__ == "__main__":
    main()
