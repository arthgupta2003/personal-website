"""Send the digest email via SMTP + STARTTLS."""

from __future__ import annotations

import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from recom.config import Settings

logger = logging.getLogger(__name__)


def send_email(subject: str, html_body: str, settings: Settings, to: str | None = None,
               cc: list[str] | None = None) -> None:
    """Send an HTML email with a plain-text fallback.

    Uses SMTP with STARTTLS as configured in *settings*.
    """
    recipient = to or settings.email_to

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = settings.email_from
    msg["To"] = recipient
    if cc:
        msg["Cc"] = ", ".join(cc)

    # Plain-text fallback (very minimal -- just tells user to view HTML)
    plain_text = (
        "Your weekly event digest is ready!\n\n"
        "This email is best viewed in an HTML-capable email client.\n"
    )
    msg.attach(MIMEText(plain_text, "plain"))
    msg.attach(MIMEText(html_body, "html"))

    all_recipients = [recipient] + (cc or [])

    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(settings.smtp_user, settings.smtp_password)
            server.sendmail(settings.email_from, all_recipients, msg.as_string())

        logger.info(
            "Email sent successfully to %s via %s:%d",
            recipient,
            settings.smtp_host,
            settings.smtp_port,
        )
    except smtplib.SMTPAuthenticationError as exc:
        logger.error("SMTP authentication failed: %s", exc)
        raise
    except smtplib.SMTPException as exc:
        logger.error("SMTP error sending email: %s", exc)
        raise
    except OSError as exc:
        logger.error("Network error sending email: %s", exc)
        raise


def send_magic_link(email: str, token: str, dashboard_url: str, settings: Settings) -> None:
    link = f"{dashboard_url}/?u={token}"
    html = f"""<div style="font-family:-apple-system,sans-serif;max-width:480px;margin:0 auto;padding:24px;">
    <h2 style="color:#1e40af;">Your Recom Link</h2>
    <p>Click below to access your events:</p>
    <a href="{link}" style="display:inline-block;padding:12px 24px;background:#2563eb;color:white;
       border-radius:8px;text-decoration:none;font-weight:600;margin:16px 0;">Open Recom</a>
    <p style="color:#9ca3af;font-size:13px;">Or copy this URL: {link}</p>
    </div>"""
    send_email("Your Recom link", html, settings, to=email)


def send_invite_email(
    email: str, token: str, group_name: str, inviter_name: str,
    group_id: int, dashboard_url: str, settings: Settings,
) -> None:
    link = f"{dashboard_url}/group/{group_id}?u={token}"
    html = f"""<div style="font-family:-apple-system,sans-serif;max-width:480px;margin:0 auto;padding:24px;">
    <h2 style="color:#1e40af;">{inviter_name} invited you to {group_name}</h2>
    <p>Join the group to see shared event picks and coordinate plans.</p>
    <a href="{link}" style="display:inline-block;padding:12px 24px;background:#2563eb;color:white;
       border-radius:8px;text-decoration:none;font-weight:600;margin:16px 0;">View Group</a>
    <p style="color:#9ca3af;font-size:13px;">Powered by Recom</p>
    </div>"""
    send_email(f"{inviter_name} invited you to {group_name}", html, settings, to=email)


def send_group_ping(
    to_email: str, to_token: str, pinger_name: str,
    event: dict, dashboard_url: str, settings: Settings,
) -> None:
    """Send a 'Bring friends?' ping email to a group member with RSVP buttons."""
    import urllib.parse as _urlparse
    title = event.get("title", "Event")
    start = event.get("start_time", "")
    start_display = start[:16].replace("T", " ") if start else "Date TBD"
    venue = event.get("location_name", "")
    reason = event.get("match_reason", "")
    event_id = event.get("event_id", "")
    event_url = event.get("url", "")
    enc_title = _urlparse.quote_plus(title[:60])

    rsvp_going = f"{dashboard_url}/api/rsvp-link?event_id={event_id}&status=going&u={to_token}&title={enc_title}"
    rsvp_maybe = f"{dashboard_url}/api/rsvp-link?event_id={event_id}&status=maybe&u={to_token}&title={enc_title}"
    dismiss_url = f"{dashboard_url}/?u={to_token}"

    subject = f"{pinger_name} is eyeing {title[:50]} \u2014 you in?"
    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f8fafc;margin:0;padding:0;">
<div style="max-width:480px;margin:0 auto;padding:32px 16px;">
  <div style="text-align:center;margin-bottom:8px;">
    <span style="font-size:11px;font-weight:700;letter-spacing:3px;color:#6366f1;text-transform:uppercase;">recom</span>
  </div>
  <h1 style="font-size:20px;font-weight:800;color:#1e293b;text-align:center;margin:0 0 6px;">
    {pinger_name} thinks this looks fun:
  </h1>
  <div style="background:white;border:2px solid #e2e8f0;border-radius:12px;padding:20px;margin:20px 0;text-align:center;">
    <p style="margin:0 0 6px;font-size:18px;font-weight:700;color:#1e293b;">
      {"<a href='" + event_url + "' style='color:#1e293b;text-decoration:none;'>" + title[:80] + "</a>" if event_url else title[:80]}
    </p>
    <p style="margin:0 0 4px;font-size:14px;color:#6b7280;">{start_display}{(' &middot; ' + venue) if venue else ''}</p>
    {('<p style="margin:8px 0 0;font-size:13px;color:#6d28d9;background:#f5f3ff;padding:8px 12px;border-radius:8px;border-left:3px solid #8b5cf6;text-align:left;">' + reason + '</p>') if reason else ''}
  </div>
  <div style="text-align:center;margin-bottom:24px;">
    <a href="{rsvp_going}" style="display:inline-block;padding:12px 28px;background:#16a34a;color:white;border-radius:10px;text-decoration:none;font-size:15px;font-weight:700;margin:0 4px;">I&rsquo;m in!</a>
    <a href="{rsvp_maybe}" style="display:inline-block;padding:12px 28px;background:#f59e0b;color:white;border-radius:10px;text-decoration:none;font-size:15px;font-weight:700;margin:0 4px;">Maybe</a>
    <a href="{dismiss_url}" style="display:inline-block;padding:12px 28px;background:#e2e8f0;color:#64748b;border-radius:10px;text-decoration:none;font-size:15px;font-weight:700;margin:0 4px;">Nah</a>
  </div>
  <div style="text-align:center;border-top:1px solid #e2e8f0;padding-top:16px;">
    <a href="{dashboard_url}/?u={to_token}" style="font-size:13px;color:#6b7280;text-decoration:none;">View your calendar &rarr;</a>
  </div>
</div>
</body></html>"""
    send_email(subject, html, settings, to=to_email)


def send_rsvp_notify(
    to_email: str, to_token: str, rsvper_name: str,
    event_title: str, event_url: str, dashboard_url: str, settings: Settings,
) -> None:
    cal_link = f"{dashboard_url}/?u={to_token}"
    html = f"""<div style="font-family:-apple-system,sans-serif;max-width:480px;margin:0 auto;padding:24px;">
    <p><strong>{rsvper_name}</strong> is going to
       <a href="{event_url}" style="color:#1e40af;">{event_title}</a></p>
    <a href="{cal_link}" style="color:#2563eb;font-size:13px;">View your calendar</a>
    </div>"""
    send_email(f"{rsvper_name} is going to {event_title[:50]}", html, settings, to=to_email)
