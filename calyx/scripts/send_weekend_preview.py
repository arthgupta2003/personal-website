#!/usr/bin/env python3
"""Send a Thursday evening "Weekend Preview" email with Fri/Sat/Sun picks.

Cron: 0 18 * * 4  (Thursday 6pm)
Only sends if there are 3+ weekend events scoring >= 25.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from calyx.config import Settings
from calyx.db import Database
from calyx.email.composer import compose_weekend_email
from calyx.email.sender import send_email
from calyx.models import RankedEvent, Event

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def _row_to_ranked_event(row: dict) -> RankedEvent:
    """Convert a joined events+rankings DB row to a RankedEvent."""
    from datetime import datetime

    st = None
    if row.get("start_time"):
        try:
            st = datetime.fromisoformat(row["start_time"])
        except (ValueError, TypeError):
            pass
    et = None
    if row.get("end_time"):
        try:
            et = datetime.fromisoformat(row["end_time"])
        except (ValueError, TypeError):
            pass

    event = Event(
        id=row.get("event_id", ""),
        title=row.get("title", ""),
        description=row.get("description", ""),
        start_time=st,
        end_time=et,
        location_name=row.get("location_name", ""),
        url=row.get("url", ""),
        source=row.get("source", "eventbrite"),
        category=row.get("category", ""),
        price=row.get("price", ""),
        image_url=row.get("image_url", ""),
    )
    return RankedEvent(
        event=event,
        score=int(row.get("score") or 0),
        match_reason=row.get("match_reason", ""),
        keep=bool(row.get("keep", True)),
        vibe=row.get("vibe", "mixed"),
    )


def send_weekend_preview(user_id: int = 1) -> bool:
    settings = Settings()
    if not settings.anthropic_api_key:
        logger.error("No API key configured")
        return False

    db = Database(settings.db_path)

    user = db.get_user(user_id)
    if not user:
        logger.error("User %d not found", user_id)
        return False

    email_to = user.get("email") or settings.email_to
    if not email_to:
        logger.error("No email configured")
        return False

    # Get latest run for user
    run = db.get_user_latest_run(user_id)
    if not run:
        logger.info("No run found for user %d", user_id)
        return False

    # Fetch all kept events from latest run
    rows = db.conn.execute(
        """SELECT e.*, rk.score, rk.match_reason, rk.vibe, rk.keep
           FROM events e
           JOIN rankings rk ON rk.event_id = e.event_id AND rk.run_id = e.run_id
           WHERE e.run_id = ? AND rk.keep = 1 AND rk.score >= 25
           ORDER BY rk.score DESC""",
        (run["id"],),
    ).fetchall()

    if not rows:
        logger.info("No ranked events for user %d", user_id)
        return False

    ranked_events = [_row_to_ranked_event(dict(r)) for r in rows]
    user_token = user.get("user_token", "")
    dashboard_url = settings.dashboard_url

    result = compose_weekend_email(ranked_events, dashboard_url=dashboard_url, user_token=user_token)
    if result is None:
        logger.info("Fewer than 3 weekend events for user %d — skipping", user_id)
        return False

    subject, html_body = result

    if settings.smtp_password:
        settings.email_to = email_to
        send_email(subject, html_body, settings)
        logger.info("Weekend preview sent to %s", email_to)
        return True
    else:
        out = Path(settings.state_dir) / "weekend_preview.html"
        out.write_text(html_body)
        logger.info("SMTP not configured — saved to %s", out)
        return False


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Send Thursday weekend preview email")
    parser.add_argument("--user", type=int, default=1)
    parser.add_argument("--all-users", action="store_true")
    args = parser.parse_args()

    settings = Settings()
    db = Database(settings.db_path)

    if args.all_users:
        users = db.get_users(active_only=True)
        for u in users:
            send_weekend_preview(u["id"])
    else:
        send_weekend_preview(args.user)
