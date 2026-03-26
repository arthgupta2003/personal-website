#!/usr/bin/env python3
"""One-time OAuth2 flow for Google Data Portability API (YouTube watch history)."""

import logging
import sys
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# Data Portability scope MUST be in its own flow (Google requirement)
SCOPES = ["https://www.googleapis.com/auth/dataportability.myactivity.youtube"]
CLIENT_SECRETS_FILE = Path("state/tokens/client_secret.json")
TOKEN_FILE = Path("state/tokens/youtube_history_token.json")


def main() -> None:
    if not CLIENT_SECRETS_FILE.exists():
        logger.error("Client secrets not found at %s", CLIENT_SECRETS_FILE)
        sys.exit(1)

    flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRETS_FILE), scopes=SCOPES)
    creds = flow.run_local_server(port=8890)

    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(creds.to_json())
    logger.info("YouTube history token saved to %s", TOKEN_FILE)


if __name__ == "__main__":
    main()
