#!/usr/bin/env python3
"""Send a "last minute tonight" email if user has no plans this evening.

Cron: 0 16 * * 5,6  (4pm Fri + Sat)
Or daily: 0 16 * * *  (skip if user already has RSVPs tonight)
"""
from __future__ import annotations

import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from recom.config import Settings
from recom.db import Database
from recom.email.sender import send_email

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

TONIGHT_SUBJECT_TEMPLATES = [
    "No plans tonight? Here are {n} ideas",
    "Free tonight? {n} things happening now",
    "{n} things to do tonight — last call",
]


def _format_time(dt: datetime | None) -> str:
    if not dt:
        return ""
    try:
        return dt.strftime("%-I:%M %p")
    except Exception:
        return ""


def send_tonight_digest(user_id: int = 1) -> bool:
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

    # Check if user already has RSVPs for tonight
    now = datetime.now()
    tonight_start = now.replace(hour=17, minute=0, second=0, microsecond=0)
    tonight_end = now.replace(hour=23, minute=59, second=0, microsecond=0)

    existing_rsvps = db.conn.execute(
        """SELECT COUNT(*) FROM rsvps r
           JOIN events e ON e.event_id = r.event_id
           WHERE r.user_id = ?
             AND r.status = 'going'
             AND e.start_time >= ? AND e.start_time <= ?""",
        (user_id, tonight_start.isoformat(), tonight_end.isoformat()),
    ).fetchone()[0]

    if existing_rsvps > 0:
        logger.info("User already has %d RSVPs tonight — skipping", existing_rsvps)
        return False

    # Get top events for tonight from latest run
    run = db.get_user_latest_run(user_id)
    if not run:
        logger.info("No run found for user %d", user_id)
        return False

    events = db.conn.execute(
        """SELECT e.*, rk.score, rk.match_reason, rk.vibe
           FROM events e
           JOIN rankings rk ON rk.event_id = e.event_id AND rk.run_id = e.run_id
           WHERE e.run_id = ?
             AND rk.keep = 1
             AND e.start_time >= ? AND e.start_time <= ?
           ORDER BY rk.score DESC
           LIMIT 5""",
        (run["id"], tonight_start.isoformat(), tonight_end.isoformat()),
    ).fetchall()

    if not events:
        logger.info("No events found for tonight")
        return False

    events = [dict(e) for e in events]
    dashboard_url = settings.dashboard_url
    token = user.get("user_token", "")

    # Build short email
    items_html = ""
    for e in events[:3]:
        title = e.get("title", "")
        time_str = _format_time(datetime.fromisoformat(e["start_time"]) if e.get("start_time") else None)
        location = e.get("location_name", "")
        score = int(e.get("score") or 0)
        reason = (e.get("match_reason") or "")[:80]
        url = e.get("url") or f"{dashboard_url}?u={token}"
        eid = e.get("event_id", "")
        rsvp_url = f"{dashboard_url}/api/rsvp-link?event_id={eid}&status=going&u={token}"

        items_html += f"""
        <tr><td style="padding:16px 20px;border-bottom:1px solid #f1f5f9;">
          <table width="100%" cellpadding="0" cellspacing="0">
            <tr>
              <td>
                <p style="margin:0 0 4px;font-size:16px;font-weight:700;color:#1e293b;">
                  <a href="{url}" style="color:#1e293b;text-decoration:none;">{title}</a>
                </p>
                <p style="margin:0 0 8px;font-size:13px;color:#64748b;">{time_str}{' · ' + location if location else ''}</p>
                {f'<p style="margin:0 0 8px;font-size:12px;color:#6d28d9;">{reason}</p>' if reason else ''}
              </td>
              <td style="text-align:right;vertical-align:top;padding-left:12px;">
                <span style="display:inline-block;background:#f1f5f9;color:#374151;font-size:11px;font-weight:800;padding:2px 8px;border-radius:8px;">{score}</span>
              </td>
            </tr>
            <tr><td colspan="2">
              <a href="{rsvp_url}" style="display:inline-block;background:#4f46e5;color:white;text-decoration:none;font-size:13px;font-weight:700;padding:7px 18px;border-radius:20px;">I'm in →</a>
            </td></tr>
          </table>
        </td></tr>"""

    n = len(events[:3])
    subject = TONIGHT_SUBJECT_TEMPLATES[0].format(n=n)

    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#0f172a;margin:0;padding:0;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#0f172a;">
<tr><td align="center" style="padding:24px 16px 40px;">
<table width="100%" style="max-width:520px;" cellpadding="0" cellspacing="0">
  <tr><td style="background:linear-gradient(135deg,#1e1b4b,#312e81);border-radius:16px 16px 0 0;padding:28px 24px;text-align:center;">
    <p style="margin:0 0 4px;font-size:11px;font-weight:700;letter-spacing:3px;color:#818cf8;text-transform:uppercase;">recom · tonight</p>
    <h1 style="margin:0 0 6px;font-size:26px;font-weight:800;color:white;line-height:1.2;">{n} things<br>happening now</h1>
    <p style="margin:0;font-size:14px;color:rgba(255,255,255,.6);">{now.strftime('%A, %B %-d')}</p>
  </td></tr>
  <tr><td style="background:white;border-radius:0 0 16px 16px;">
    <table width="100%" cellpadding="0" cellspacing="0">
      {items_html}
    </table>
    <div style="padding:16px 20px;text-align:center;">
      <a href="{dashboard_url}?u={token}" style="font-size:13px;color:#6b7280;">See all events →</a>
    </div>
  </td></tr>
</table>
</td></tr>
</table>
</body></html>"""

    try:
        settings.email_to = email_to
        send_email(subject, html, settings)
        logger.info("Tonight digest sent to %s (%d events)", email_to, n)
        return True
    except Exception as exc:
        logger.error("Failed to send tonight digest: %s", exc)
        return False


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Send last-minute tonight email")
    parser.add_argument("--user", type=int, default=1)
    parser.add_argument("--all-users", action="store_true")
    args = parser.parse_args()

    settings = Settings()
    db = Database(settings.db_path)

    if args.all_users:
        users = db.get_users(active_only=True)
        for u in users:
            send_tonight_digest(u["id"])
    else:
        send_tonight_digest(args.user)
