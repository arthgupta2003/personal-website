#!/usr/bin/env python3
"""
Post-event rating email sender.
Cron: run daily at 10pm to find events that ended recently and prompt for ratings.

Usage:
  uv run python scripts/send_ratings.py          # dry run (print, don't send)
  uv run python scripts/send_ratings.py --send   # actually send emails
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from calyx.config import Settings
from calyx.db import Database


def get_events_needing_rating(db: Database, user_id: int) -> list[dict]:
    """Return events where user RSVP'd 'going', event ended within last 48h, no rating yet."""
    now = datetime.now()
    cutoff = (now - timedelta(hours=48)).isoformat()
    # Events that ended before now but after cutoff (48h window)
    rows = db.conn.execute(
        """SELECT r.event_id, e.title, e.start_time, e.location_name, e.url
           FROM rsvps r
           JOIN events e ON e.event_id = r.event_id
           LEFT JOIN attended a ON a.event_id = r.event_id AND a.user_id = r.user_id
           WHERE r.user_id = ?
             AND r.status = 'going'
             AND e.start_time < ?
             AND e.start_time > ?
             AND (a.id IS NULL OR a.rating IS NULL)
           ORDER BY e.start_time DESC""",
        (user_id, now.isoformat(), cutoff),
    ).fetchall()
    return [dict(r) for r in rows]


def format_rating_email(user: dict, events: list[dict], settings: Settings) -> tuple[str, str]:
    """Return (subject, html_body) for the rating email."""
    dashboard_url = settings.dashboard_url
    token = user.get("user_token", "")
    name = user.get("name") or user.get("email", "")

    if len(events) == 1:
        ev = events[0]
        subject = f"How was {ev['title'][:40]}?"
    else:
        subject = f"How were your {len(events)} recent events?"

    event_html = ""
    for ev in events:
        title = ev.get("title", "")[:60]
        eid = ev.get("event_id", "")
        venue = ev.get("location_name", "")
        star_links = " ".join(
            f'<a href="{dashboard_url}/api/rate?event_id={eid}&rating={r}&u={token}" '
            f'style="display:inline-block;padding:8px 14px;margin:2px;background:#1e293b;'
            f'color:white;border-radius:8px;text-decoration:none;font-size:18px;">{"★"*r}</a>'
            for r in range(1, 6)
        )
        no_go_link = (
            f'<a href="{dashboard_url}/api/rate?event_id={eid}&rating=0&u={token}&no_go=1" '
            f'style="display:inline-block;padding:6px 12px;margin:2px;background:#f3f4f6;'
            f'color:#6b7280;border-radius:8px;text-decoration:none;font-size:13px;">I didn&apos;t go</a>'
        )
        event_html += f"""
        <div style="margin-bottom:28px;padding:20px;background:#f8fafc;border-radius:12px;border:1px solid #e2e8f0;">
            <div style="font-size:17px;font-weight:700;color:#1e293b;margin-bottom:4px;">{title}</div>
            {f'<div style="font-size:13px;color:#6b7280;margin-bottom:12px;">{venue}</div>' if venue else ''}
            <div style="margin-bottom:8px;font-size:14px;color:#374151;font-weight:600;">How was it?</div>
            <div style="margin-bottom:8px;">{star_links}</div>
            <div>{no_go_link}</div>
        </div>"""

    html_body = f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#fff;margin:0;padding:0;">
<div style="max-width:520px;margin:0 auto;padding:32px 16px;">
    <div style="font-size:14px;color:#6b7280;margin-bottom:4px;">Hi {name}!</div>
    <h1 style="font-size:22px;font-weight:800;color:#1e293b;margin:0 0 24px;">
        {f"How was {events[0]['title'][:40]}?" if len(events) == 1 else f"How were your recent events?"}
    </h1>
    {event_html}
    <div style="margin-top:24px;padding-top:16px;border-top:1px solid #e2e8f0;">
        <a href="{dashboard_url}/attended" style="font-size:13px;color:#6b7280;text-decoration:none;">
            View all your attended events →
        </a>
    </div>
</div>
</body>
</html>"""

    return subject, html_body


def main():
    parser = argparse.ArgumentParser(description="Send post-event rating emails")
    parser.add_argument("--send", action="store_true", help="Actually send emails (default: dry run)")
    parser.add_argument("--user", type=int, default=None, help="Only process this user ID")
    args = parser.parse_args()

    settings = Settings()
    db = Database(settings.db_path)

    users = db.get_users(active_only=True)
    if args.user:
        users = [u for u in users if u["id"] == args.user]

    total_sent = 0
    for user in users:
        if not user.get("email"):
            continue
        events = get_events_needing_rating(db, user["id"])
        if not events:
            continue

        subject, html_body = format_rating_email(user, events, settings)
        print(f"\nUser {user['id']} ({user['email']}): {len(events)} events to rate")
        print(f"  Subject: {subject}")
        for ev in events:
            print(f"  - {ev['title'][:50]} ({ev.get('start_time','')[:10]})")

        if args.send:
            try:
                from calyx.email.sender import send_email
                send_email(
                    to=user["email"],
                    subject=subject,
                    html_body=html_body,
                    settings=settings,
                )
                print(f"  ✓ Sent to {user['email']}")
                total_sent += 1
            except Exception as exc:
                print(f"  ✗ Failed to send: {exc}")
        else:
            print("  (dry run — use --send to send)")

    if args.send:
        print(f"\nSent {total_sent} rating email(s)")
    else:
        print("\nDry run complete. Use --send to send emails.")


if __name__ == "__main__":
    main()
