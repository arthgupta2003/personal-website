"""Send daily event digest email from the latest pipeline run."""
from __future__ import annotations

import json
import logging
import sys
from datetime import datetime
from pathlib import Path

from calyx.config import Settings
from calyx.db import Database
from calyx.email.composer import compose_daily_email
from calyx.email.sender import send_email
from calyx.models import Event, RankedEvent

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def _reconstruct_ranked(events_data: list[dict]) -> list[RankedEvent]:
    """Reconstruct RankedEvent list from DB rows."""
    ranked = []
    for ed in events_data:
        raw = json.loads(ed["raw_json"]) if ed.get("raw_json") else {}
        if not raw or "id" not in raw:
            # User-added events store raw_json='{}' — reconstruct from DB columns
            source = ed.get("source", "eventbrite")
            if source == "user":
                source = "eventbrite"  # placeholder — Event.source is an enum
            raw = {
                "id": ed["event_id"],
                "source": source,
                "title": ed.get("title") or "",
                "description": ed.get("description") or "",
                "url": ed.get("url") or "",
                "start_time": ed.get("start_time"),
                "end_time": ed.get("end_time"),
                "location_name": ed.get("location_name") or "",
                "location_address": ed.get("location_address") or "",
                "is_online": bool(ed.get("is_online", False)),
                "price": ed.get("price"),
                "category": ed.get("category"),
                "image_url": ed.get("image_url"),
            }
        event = Event.model_validate(raw)
        ranked.append(RankedEvent(
            event=event,
            score=ed.get("score") or 0,
            interest_score=ed.get("interest_score") or 0,
            social_score=ed.get("social_score") or 0,
            urgency_score=ed.get("urgency_score") or 0,
            logistics_score=ed.get("logistics_score") or 0,
            friend_score=ed.get("friend_score") or 0,
            discovery_score=ed.get("discovery_score") or 0,
            quality_score=ed.get("quality_score") or 0,
            vibe=ed.get("vibe") or "mixed",
            match_reason=ed.get("match_reason") or "",
            keep=bool(ed.get("keep", 0)),
            filter_reason=ed.get("filter_reason"),
            event_type=ed.get("event_type") or "event",
        ))
    return ranked


def send_daily_for_user(db: Database, settings: Settings, user: dict, today: datetime):
    """Send daily email for a specific user."""
    user_id = user["id"]
    user_token = user.get("user_token") or ""
    user_label = user["name"] or user["email"]

    # Find latest run for this user
    row = db.conn.execute(
        "SELECT id FROM runs WHERE user_id = ? ORDER BY timestamp DESC LIMIT 1",
        (user_id,),
    ).fetchone()
    if not row:
        logger.info(f"No runs for user {user_label} — skipping")
        return
    run_id = row["id"]

    # Ensure daily_picks are computed for this run
    picks = db.get_daily_picks(run_id)
    if not picks:
        db.compute_daily_picks(run_id, user_id)
        picks = db.get_daily_picks(run_id)

    events_data = db.get_run_events(run_id)
    ranked = _reconstruct_ranked(events_data)

    # Fetch friend RSVPs for events happening today
    event_ids = [r.event.id for r in ranked if r.keep]
    friend_rsvps = db.get_rsvps_for_events(event_ids)

    # Bucket suggestions on weekends
    bucket_suggestions = []
    if today.weekday() >= 5:
        try:
            import anthropic
            from calyx.ranking.bucket_list import load_bucket_list, pick_suggestions
            client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
            items = load_bucket_list(settings.bucket_list_file)
            if items:
                bucket_suggestions, _ = pick_suggestions(items, client, settings.claude_model)
        except Exception:
            logger.exception("Failed to get bucket suggestions")

    # Pass only TODAY's daily_pick event IDs (picks span all days)
    today_str = today.strftime("%Y-%m-%d")
    pick_ids = {p["event_id"] for p in picks if p.get("day") == today_str} if picks else None

    result = compose_daily_email(
        ranked, today,
        bucket_suggestions=bucket_suggestions,
        user_token=user_token,
        friend_rsvps=friend_rsvps,
        daily_pick_ids=pick_ids,
    )
    if result is None:
        logger.info(f"No events for {user_label} today — skipping")
        return

    subject, html_body = result

    if settings.smtp_password:
        settings.email_to = user["email"]
        send_email(subject, html_body, settings)
        logger.info(f"Daily email sent to {user_label}: {subject}")
    else:
        path = Path(settings.state_dir) / f"daily_{user_label}_{today.strftime('%Y-%m-%d')}.html"
        path.write_text(html_body)
        logger.info(f"SMTP not configured — saved to {path}")


def send_group_digests(db: Database, settings: Settings, today: datetime):
    """Send group digest emails — one per group, CC'd to all members."""
    from calyx.email.composer import compose_daily_email

    groups = db.conn.execute("SELECT id, name, slug FROM groups").fetchall()
    target_str = today.strftime("%Y-%m-%d")

    for group in groups:
        members = db.get_group_members(group["id"])
        if len(members) < 2:
            continue

        # Use the first member's run to get group events
        # (group events blend all members' latest runs)
        events_data = db.get_group_events(group["id"])
        if not events_data:
            continue

        ranked = _reconstruct_ranked(events_data)

        # Get all RSVPs from group members for context
        event_ids = [r.event.id for r in ranked if r.keep]
        friend_rsvps = db.get_rsvps_for_events(event_ids)

        # Filter to today's events, apply same curation
        todays = [r for r in ranked if r.keep and r.score >= 40
                  and r.event.start_time is not None
                  and r.event.start_time.strftime("%Y-%m-%d") == target_str]
        if not todays:
            continue

        # Use first member's token for RSVP links
        first_token = members[0].get("user_token", "")

        result = compose_daily_email(
            ranked, today,
            user_token=first_token,
            friend_rsvps=friend_rsvps,
        )
        if result is None:
            continue

        subject, html_body = result
        group_name = group["name"] or group["slug"]
        subject = f"[{group_name}] {subject}"

        member_emails = [m["email"] for m in members if m.get("email")]
        if not member_emails:
            continue

        # Send to first member, CC rest
        to = member_emails[0]
        cc = member_emails[1:] if len(member_emails) > 1 else None

        if settings.smtp_password:
            send_email(subject, html_body, settings, to=to, cc=cc)
            logger.info(f"Group digest sent for '{group_name}' to {len(member_emails)} members")
        else:
            logger.info(f"SMTP not configured — skipping group digest for '{group_name}'")


def main():
    settings = Settings()
    db = Database(settings.db_path)
    today = datetime.now()

    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--all-users", action="store_true", help="Send to all active users")
    parser.add_argument("--user", type=int, help="Send to specific user ID")
    args, _ = parser.parse_known_args()

    if args.all_users:
        users = db.get_users(active_only=True)
        logger.info(f"Sending daily emails to {len(users)} users")
        for user in users:
            try:
                send_daily_for_user(db, settings, user, today)
            except Exception:
                logger.exception(f"Failed for user {user['email']}")
        # Send group digests after individual emails
        try:
            send_group_digests(db, settings, today)
        except Exception:
            logger.exception("Failed sending group digests")
    else:
        user_id = args.user or 1
        user = db.get_user(user_id)
        if not user:
            logger.error(f"User {user_id} not found")
            return
        send_daily_for_user(db, settings, user, today)

    db.close()


if __name__ == "__main__":
    main()
