"""Gmail newsletter scanner — fetches recent emails from known newsletter senders."""

from __future__ import annotations

import base64
import logging
from datetime import datetime
from pathlib import Path

from recom.config import Settings

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


def _load_credentials(settings: Settings):
    """Load saved Gmail OAuth2 credentials from the token file."""
    from google.oauth2.credentials import Credentials

    token_path = Path(settings.gmail_token_file)
    if not token_path.exists():
        logger.warning(
            "Gmail token file not found at %s. Run scripts/auth_gmail.py first.",
            token_path,
        )
        return None

    try:
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
    except Exception:
        logger.exception("Failed to load Gmail credentials")
        return None

    if creds.expired and creds.refresh_token:
        try:
            from google.auth.transport.requests import Request

            creds.refresh(Request())
            token_path.write_text(creds.to_json())
        except Exception:
            logger.exception("Failed to refresh Gmail credentials")
            return None

    return creds


def _load_newsletter_senders(settings: Settings) -> list[str]:
    """Read newsletter senders from the config file (one per line, # for comments)."""
    senders_path = Path(settings.newsletter_senders_file)
    if not senders_path.exists():
        logger.warning("Newsletter senders file not found at %s", senders_path)
        return []

    senders: list[str] = []
    try:
        for line in senders_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                senders.append(line)
    except Exception:
        logger.exception("Failed to read newsletter senders file")

    logger.info("Loaded %d newsletter senders", len(senders))
    return senders


def _extract_html_body(payload: dict) -> str:
    """Recursively extract the HTML body from a Gmail message payload."""
    mime_type = payload.get("mimeType", "")

    # Direct HTML part
    if mime_type == "text/html":
        data = payload.get("body", {}).get("data", "")
        if data:
            return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")

    # Multipart: recurse into parts
    for part in payload.get("parts", []):
        html = _extract_html_body(part)
        if html:
            return html

    return ""


def _parse_date(headers: list[dict]) -> str | None:
    """Extract the Date header value from Gmail message headers."""
    for header in headers:
        if header.get("name", "").lower() == "date":
            return header.get("value")
    return None


def _parse_subject(headers: list[dict]) -> str:
    """Extract the Subject header value from Gmail message headers."""
    for header in headers:
        if header.get("name", "").lower() == "subject":
            return header.get("value", "(no subject)")
    return "(no subject)"


def get_newsletter_emails(settings: Settings) -> list[dict]:
    """Fetch recent newsletter emails from known senders.

    Returns a list of dicts with keys: sender, subject, html_body, date.
    Returns an empty list if credentials are missing or an error occurs.
    """
    creds = _load_credentials(settings)
    if creds is None:
        return []

    senders = _load_newsletter_senders(settings)
    if not senders:
        return []

    try:
        from googleapiclient.discovery import build
    except ImportError:
        logger.warning("google-api-python-client not installed; skipping Gmail ingest")
        return []

    results: list[dict] = []

    try:
        gmail = build("gmail", "v1", credentials=creds)

        for sender in senders:
            try:
                query = f"from:{sender} newer_than:7d"
                resp = gmail.users().messages().list(userId="me", q=query, maxResults=10).execute()
                message_ids = [m["id"] for m in resp.get("messages", [])]

                for msg_id in message_ids:
                    try:
                        msg = (
                            gmail.users()
                            .messages()
                            .get(userId="me", id=msg_id, format="full")
                            .execute()
                        )
                        payload = msg.get("payload", {})
                        headers = payload.get("headers", [])

                        results.append(
                            {
                                "sender": sender,
                                "subject": _parse_subject(headers),
                                "html_body": _extract_html_body(payload),
                                "date": _parse_date(headers),
                            }
                        )
                    except Exception:
                        logger.exception("Failed to fetch Gmail message %s", msg_id)

            except Exception:
                logger.exception("Failed to query Gmail for sender %s", sender)

    except Exception:
        logger.exception("Gmail API service creation failed")

    logger.info("Fetched %d newsletter emails", len(results))
    return results
