"""Google Calendar integration — push events, manage attendees, sync RSVPs."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from calyx.config import Settings
from calyx.db import Database

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/calendar"]
CALENDAR_SUMMARY = "Recom Picks"
CALENDAR_DESCRIPTION = "Events recommended by Recom"


def _get_credentials(settings: Settings) -> Credentials | None:
    """Load and refresh Google Calendar OAuth credentials."""
    token_path = Path(settings.gcal_token_file)
    if not token_path.exists():
        logger.warning("GCal token not found at %s — run scripts/auth_gcal.py first", token_path)
        return None

    creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        token_path.write_text(creds.to_json())
        logger.info("Refreshed GCal credentials")
    return creds


def _get_service(settings: Settings):
    """Build the Google Calendar API service."""
    creds = _get_credentials(settings)
    if not creds:
        return None
    return build("calendar", "v3", credentials=creds)


def get_or_create_calendar(settings: Settings) -> str | None:
    """Get the Recom shared calendar ID, creating it if needed.

    Returns the calendar ID or None if GCal is not configured.
    """
    if settings.gcal_calendar_id:
        return settings.gcal_calendar_id

    service = _get_service(settings)
    if not service:
        return None

    # Check if calendar already exists
    calendars = service.calendarList().list().execute()
    for cal in calendars.get("items", []):
        if cal.get("summary") == CALENDAR_SUMMARY:
            cal_id = cal["id"]
            logger.info("Found existing Recom calendar: %s", cal_id)
            return cal_id

    # Create new calendar
    body = {
        "summary": CALENDAR_SUMMARY,
        "description": CALENDAR_DESCRIPTION,
        "timeZone": "America/New_York",
    }
    created = service.calendars().insert(body=body).execute()
    cal_id = created["id"]
    logger.info("Created Recom calendar: %s", cal_id)
    return cal_id


def push_event(
    settings: Settings,
    db: Database,
    calendar_id: str,
    event_id: str,
    title: str,
    start_time: str | datetime | None,
    end_time: str | datetime | None = None,
    location: str = "",
    description: str = "",
    url: str = "",
    attendee_emails: list[str] | None = None,
) -> str | None:
    """Push a single event to Google Calendar and store the mapping.

    Returns the GCal event ID or None on failure.
    """
    service = _get_service(settings)
    if not service:
        return None

    # Check if already pushed
    existing = db.get_gcal_event(event_id)
    if existing:
        logger.debug("Event %s already in GCal as %s", event_id, existing["gcal_event_id"])
        return existing["gcal_event_id"]

    # Parse start/end times
    start_dt = _parse_dt(start_time)
    if not start_dt:
        logger.warning("Cannot push event %s — no start time", event_id)
        return None

    end_dt = _parse_dt(end_time) if end_time else start_dt + timedelta(hours=2)

    body: dict = {
        "summary": title,
        "start": {"dateTime": start_dt.isoformat(), "timeZone": "America/New_York"},
        "end": {"dateTime": end_dt.isoformat(), "timeZone": "America/New_York"},
    }

    if location:
        body["location"] = location
    if description or url:
        desc_parts = []
        if description:
            desc_parts.append(description[:500])
        if url:
            desc_parts.append(f"\nMore info: {url}")
        body["description"] = "\n".join(desc_parts)
    if attendee_emails:
        body["attendees"] = [{"email": e} for e in attendee_emails]

    try:
        result = service.events().insert(
            calendarId=calendar_id,
            body=body,
            sendUpdates="all" if attendee_emails else "none",
        ).execute()
        gcal_event_id = result["id"]
        db.set_gcal_event(event_id, gcal_event_id, calendar_id)
        logger.info("Pushed event '%s' to GCal: %s", title, gcal_event_id)
        return gcal_event_id
    except Exception:
        logger.exception("Failed to push event '%s' to GCal", title)
        return None


def update_attendees(
    settings: Settings,
    db: Database,
    event_id: str,
    attendee_emails: list[str],
) -> bool:
    """Update the attendee list for an event already in GCal."""
    service = _get_service(settings)
    if not service:
        return False

    mapping = db.get_gcal_event(event_id)
    if not mapping:
        logger.warning("Event %s not in GCal — push it first", event_id)
        return False

    try:
        gcal_event = service.events().get(
            calendarId=mapping["gcal_calendar_id"],
            eventId=mapping["gcal_event_id"],
        ).execute()

        # Merge new attendees with existing
        existing_emails = {a["email"] for a in gcal_event.get("attendees", [])}
        all_attendees = list(gcal_event.get("attendees", []))
        for email in attendee_emails:
            if email not in existing_emails:
                all_attendees.append({"email": email})

        gcal_event["attendees"] = all_attendees
        service.events().update(
            calendarId=mapping["gcal_calendar_id"],
            eventId=mapping["gcal_event_id"],
            body=gcal_event,
            sendUpdates="all",
        ).execute()
        logger.info("Updated attendees for event %s", event_id)
        return True
    except Exception:
        logger.exception("Failed to update attendees for event %s", event_id)
        return False


def get_rsvp_status(
    settings: Settings,
    db: Database,
    event_id: str,
) -> dict[str, str]:
    """Read RSVP status from GCal for a given event.

    Returns {email: status} where status is one of:
    needsAction, accepted, tentative, declined.
    """
    service = _get_service(settings)
    if not service:
        return {}

    mapping = db.get_gcal_event(event_id)
    if not mapping:
        return {}

    try:
        gcal_event = service.events().get(
            calendarId=mapping["gcal_calendar_id"],
            eventId=mapping["gcal_event_id"],
        ).execute()
        return {
            a["email"]: a.get("responseStatus", "needsAction")
            for a in gcal_event.get("attendees", [])
        }
    except Exception:
        logger.exception("Failed to get RSVP status for event %s", event_id)
        return {}


def sync_rsvps_to_db(
    settings: Settings,
    db: Database,
    event_id: str,
    run_id: int,
) -> int:
    """Sync GCal attendee responses back to the local RSVP table.

    Returns the number of RSVPs synced.
    """
    gcal_statuses = get_rsvp_status(settings, db, event_id)
    if not gcal_statuses:
        return 0

    # Map GCal response status to our RSVP status
    STATUS_MAP = {
        "accepted": "going",
        "tentative": "maybe",
        "declined": "cant",
    }

    synced = 0
    for email, gcal_status in gcal_statuses.items():
        local_status = STATUS_MAP.get(gcal_status)
        if not local_status:
            continue  # skip needsAction
        user = db.get_user_by_email(email)
        if not user:
            continue
        db.set_rsvp(user["id"], event_id, run_id, local_status)
        synced += 1

    if synced:
        logger.info("Synced %d RSVPs from GCal for event %s", synced, event_id)
    return synced


def push_rsvped_events(
    settings: Settings,
    db: Database,
    user_id: int,
    run_id: int,
) -> int:
    """Push all events a user has RSVP'd 'going' to their Google Calendar.

    Returns number of events pushed.
    """
    calendar_id = get_or_create_calendar(settings)
    if not calendar_id:
        return 0

    # Get user's going RSVPs for this run
    rows = db.conn.execute(
        """SELECT r.event_id, e.title, e.start_time, e.end_time,
                  e.location_name, e.location_address, e.description, e.url
           FROM rsvps r
           JOIN events e ON e.event_id = r.event_id AND e.run_id = r.run_id
           WHERE r.user_id = ? AND r.run_id = ? AND r.status = 'going'""",
        (user_id, run_id),
    ).fetchall()

    pushed = 0
    for row in rows:
        row = dict(row)
        location = row["location_name"]
        if row["location_address"]:
            location = f"{location}, {row['location_address']}" if location else row["location_address"]

        result = push_event(
            settings=settings,
            db=db,
            calendar_id=calendar_id,
            event_id=row["event_id"],
            title=row["title"],
            start_time=row["start_time"],
            end_time=row["end_time"],
            location=location,
            description=row["description"] or "",
            url=row["url"] or "",
        )
        if result:
            pushed += 1

    logger.info("Pushed %d/%d RSVP'd events to GCal for user %d", pushed, len(rows), user_id)
    return pushed


def share_calendar(settings: Settings, calendar_id: str, email: str, role: str = "reader") -> bool:
    """Share the Recom calendar with a user."""
    service = _get_service(settings)
    if not service:
        return False

    try:
        service.acl().insert(
            calendarId=calendar_id,
            body={"role": role, "scope": {"type": "user", "value": email}},
        ).execute()
        logger.info("Shared calendar %s with %s (role=%s)", calendar_id, email, role)
        return True
    except Exception:
        logger.exception("Failed to share calendar with %s", email)
        return False


def _parse_dt(raw: str | datetime | None) -> datetime | None:
    """Parse a datetime from string or return as-is."""
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return raw
    for fmt in (
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d",
    ):
        try:
            return datetime.strptime(raw.strip(), fmt)
        except ValueError:
            continue
    return None
