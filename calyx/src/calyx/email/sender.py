"""Send the digest email via SMTP + STARTTLS."""

from __future__ import annotations

import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from calyx.config import Settings

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


def send_invite_email(
    email: str, token: str, group_name: str, inviter_name: str,
    group_id: int, dashboard_url: str, settings: Settings,
    invite_code: str = "",
) -> None:
    link = f"{dashboard_url}/group/{group_id}/join/{invite_code}" if invite_code else f"{dashboard_url}/group/{group_id}"
    html = f"""<div style="font-family:-apple-system,sans-serif;max-width:480px;margin:0 auto;padding:24px;">
    <h2 style="color:#1e40af;">{inviter_name} invited you to {group_name}</h2>
    <p>Join the group to see shared event picks and coordinate plans.</p>
    <a href="{link}" style="display:inline-block;padding:12px 24px;background:#2563eb;color:white;
       border-radius:8px;text-decoration:none;font-weight:600;margin:16px 0;">Join Group</a>
    <p style="color:#9ca3af;font-size:13px;">Powered by Calyx</p>
    </div>"""
    send_email(f"{inviter_name} invited you to {group_name}", html, settings, to=email)



def send_group_event_notification(
    to_emails: list[str], adder_name: str, event_title: str,
    event_date: str, group_name: str, group_id: int,
    dashboard_url: str, settings: Settings,
) -> None:
    """Notify group members when someone adds an event to a group."""
    group_link = f"{dashboard_url}/group/{group_id}"
    subject = f"{adder_name} added '{event_title}' to {group_name}"
    html = f"""<div style="font-family:-apple-system,sans-serif;max-width:480px;margin:0 auto;padding:24px;">
    <h2 style="color:#1e40af;">New event in {group_name}</h2>
    <div style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:10px;padding:16px;margin:16px 0;">
      <p style="margin:0 0 4px;font-size:17px;font-weight:700;color:#1e293b;">{event_title}</p>
      <p style="margin:0;font-size:14px;color:#6b7280;">{event_date}</p>
    </div>
    <p style="color:#374151;font-size:14px;">Added by <strong>{adder_name}</strong></p>
    <a href="{group_link}" style="display:inline-block;padding:12px 24px;background:#2563eb;color:white;
       border-radius:8px;text-decoration:none;font-weight:600;margin:16px 0;">View Group</a>
    <p style="color:#9ca3af;font-size:13px;">Powered by Calyx</p>
    </div>"""
    for email in to_emails:
        send_email(subject, html, settings, to=email)


def send_rsvp_notify(
    to_email: str, to_token: str, rsvper_name: str,
    event_title: str, event_url: str, dashboard_url: str, settings: Settings,
    *, event_when: str = "", event_location: str = "", group_name: str = "",
) -> None:
    """Notify a group-mate that someone RSVP'd 'going'. Tasteful Calyx-design email
    with a sage header, event card, and clear primary action."""
    from html import escape
    cal_link = f"{dashboard_url}/?u={to_token}"
    initial = (rsvper_name[:1] or "?").upper()
    first = (rsvper_name.split() or [rsvper_name])[0]
    title_link_open = f'<a href="{event_url}" style="color:#1a1a1a;text-decoration:none;">' if event_url else ''
    title_link_close = '</a>' if event_url else ''
    meta_bits = []
    if event_when:
        meta_bits.append(escape(event_when))
    if event_location:
        meta_bits.append(escape(event_location))
    meta_line = (" &middot; ".join(meta_bits)) or ""
    group_tag = f'<span style="display:inline-block;font-size:10px;font-weight:700;letter-spacing:1.5px;text-transform:uppercase;color:#4a6741;background:#edf2eb;padding:3px 10px;margin-bottom:14px;">{escape(group_name)}</span>' if group_name else ""

    html = f"""\
<div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#fafafa;padding:32px 20px;">
  <div style="max-width:480px;margin:0 auto;background:#fff;border:1px solid #e0e0e0;">
    <div style="padding:24px 24px 8px;">
      {group_tag}
      <div style="display:flex;align-items:center;gap:12px;margin-bottom:18px;">
        <div style="width:40px;height:40px;background:#edf2eb;color:#4a6741;display:flex;align-items:center;justify-content:center;font-size:16px;font-weight:800;border-radius:50%;flex-shrink:0;">{escape(initial)}</div>
        <div style="font-size:15px;color:#1a1a1a;line-height:1.4;"><strong style="font-weight:700;">{escape(first)}</strong> is going.</div>
      </div>
      <div style="border-left:3px solid #4a6741;padding:6px 14px;margin-bottom:20px;">
        <div style="font-size:17px;font-weight:700;color:#1a1a1a;line-height:1.3;">{title_link_open}{escape(event_title)}{title_link_close}</div>
        {f'<div style="font-size:13px;color:#6b7280;margin-top:4px;">{meta_line}</div>' if meta_line else ''}
      </div>
      <a href="{cal_link}" style="display:inline-block;padding:11px 22px;background:#4a6741;color:#fff;text-decoration:none;font-weight:700;font-size:13px;letter-spacing:.5px;text-transform:uppercase;">Open in Calyx &rarr;</a>
    </div>
    <div style="padding:14px 24px;background:#fafafa;border-top:1px solid #f0f0f0;font-size:11px;color:#999;letter-spacing:1.5px;text-transform:uppercase;">Calyx</div>
  </div>
</div>"""
    subject = f"{first} is going — {event_title[:60]}"
    send_email(subject, html, settings, to=to_email)
