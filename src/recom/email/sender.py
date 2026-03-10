"""Send the digest email via SMTP + STARTTLS."""

from __future__ import annotations

import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from recom.config import Settings

logger = logging.getLogger(__name__)


def send_email(subject: str, html_body: str, settings: Settings, to: str | None = None) -> None:
    """Send an HTML email with a plain-text fallback.

    Uses SMTP with STARTTLS as configured in *settings*.
    """
    recipient = to or settings.email_to

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = settings.email_from
    msg["To"] = recipient

    # Plain-text fallback (very minimal -- just tells user to view HTML)
    plain_text = (
        "Your weekly event digest is ready!\n\n"
        "This email is best viewed in an HTML-capable email client.\n"
    )
    msg.attach(MIMEText(plain_text, "plain"))
    msg.attach(MIMEText(html_body, "html"))

    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(settings.smtp_user, settings.smtp_password)
            server.sendmail(settings.email_from, recipient, msg.as_string())

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
    slug: str, dashboard_url: str, settings: Settings,
) -> None:
    link = f"{dashboard_url}/group/{slug}?u={token}"
    html = f"""<div style="font-family:-apple-system,sans-serif;max-width:480px;margin:0 auto;padding:24px;">
    <h2 style="color:#1e40af;">{inviter_name} invited you to {group_name}</h2>
    <p>Join the group to see shared event picks and coordinate plans.</p>
    <a href="{link}" style="display:inline-block;padding:12px 24px;background:#2563eb;color:white;
       border-radius:8px;text-decoration:none;font-weight:600;margin:16px 0;">View Group</a>
    <p style="color:#9ca3af;font-size:13px;">Powered by Recom</p>
    </div>"""
    send_email(f"{inviter_name} invited you to {group_name}", html, settings, to=email)


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
