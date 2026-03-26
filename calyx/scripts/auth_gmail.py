#!/usr/bin/env python3
"""One-time OAuth2 flow for Gmail API (read-only).

Run this script to authenticate and save credentials:
    python scripts/auth_gmail.py

Prerequisites:
    - Place your OAuth client secrets at state/tokens/client_secret.json
    - The file should be a Google Cloud OAuth 2.0 Client ID (Desktop app type)
    - Enable the Gmail API in your Google Cloud project
"""

import logging
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

CLIENT_SECRETS_FILE = Path("state/tokens/client_secret.json")
TOKEN_FILE = Path("state/tokens/gmail_token.json")


def main() -> None:
    if not CLIENT_SECRETS_FILE.exists():
        logger.error(
            "Client secrets file not found at %s. "
            "Download it from Google Cloud Console (APIs & Services > Credentials).",
            CLIENT_SECRETS_FILE,
        )
        sys.exit(1)

    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:
        logger.error("google-auth-oauthlib is not installed. Run: pip install google-auth-oauthlib")
        sys.exit(1)

    flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRETS_FILE), scopes=SCOPES)
    creds = flow.run_local_server(port=0)

    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(creds.to_json())
    logger.info("Gmail credentials saved to %s", TOKEN_FILE)


if __name__ == "__main__":
    main()
