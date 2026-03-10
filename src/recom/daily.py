"""Send daily event digest email from the latest pipeline run."""
from __future__ import annotations

import json
import logging
import sys
from datetime import datetime
from pathlib import Path

from recom.config import Settings
from recom.db import Database
from recom.email.composer import compose_daily_email
from recom.email.sender import send_email
from recom.models import Event, RankedEvent

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def _reconstruct_ranked(events_data: list[dict]) -> list[RankedEvent]:
    """Reconstruct RankedEvent list from DB rows."""
    ranked = []
    for ed in events_data:
        raw = json.loads(ed["raw_json"])
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
            from recom.ranking.bucket_list import load_bucket_list, pick_suggestions
            client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
            items = load_bucket_list(settings.bucket_list_file)
            if items:
                bucket_suggestions, _ = pick_suggestions(items, client, settings.claude_model)
        except Exception:
            logger.exception("Failed to get bucket suggestions")

    result = compose_daily_email(
        ranked, today,
        bucket_suggestions=bucket_suggestions,
        user_token=user_token,
        friend_rsvps=friend_rsvps,
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
