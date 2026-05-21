"""Calendar-invite emails (METHOD:REQUEST / CANCEL) for instant gcal sync.

Gmail and Apple Mail detect `text/calendar; method=REQUEST` parts and auto-add
the VEVENT to the recipient's primary calendar within seconds — no Google
Calendar OAuth or iCal subscription refresh required.
"""

from __future__ import annotations

import logging
import smtplib
import uuid
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from zoneinfo import ZoneInfo

from calyx.config import Settings

logger = logging.getLogger(__name__)

LOCAL_TZ = ZoneInfo("America/New_York")


def _esc(text: str) -> str:
    return (str(text or "")
            .replace("\\", "\\\\")
            .replace(";", "\\;")
            .replace(",", "\\,")
            .replace("\n", "\\n"))


def _fold(line: str) -> str:
    encoded = line.encode("utf-8")
    if len(encoded) <= 75:
        return line
    chunks = []
    while len(encoded) > 75:
        cut = 75
        while cut > 0 and (encoded[cut] & 0xC0) == 0x80:
            cut -= 1
        if cut == 0:
            cut = 75
        chunks.append(encoded[:cut].decode("utf-8"))
        encoded = encoded[cut:]
    if encoded:
        chunks.append(encoded.decode("utf-8"))
    return "\r\n ".join(chunks)


def _to_utc(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        d = datetime.fromisoformat(raw)
    except (ValueError, TypeError):
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=LOCAL_TZ)
    return d.astimezone(timezone.utc)


def _fmt_utc(d: datetime) -> str:
    return d.strftime("%Y%m%dT%H%M%SZ")


def build_invite_ics(
    *,
    event_uid: str,
    title: str,
    start_time: str | None,
    end_time: str | None,
    location: str = "",
    description: str = "",
    url: str = "",
    organizer_email: str,
    organizer_name: str = "Calyx",
    attendee_email: str,
    attendee_name: str = "",
    attendee_partstat: str = "NEEDS-ACTION",
    method: str = "REQUEST",
    sequence: int = 0,
) -> str:
    """Build a single-VEVENT iCalendar payload suitable for email-invite delivery."""
    dtstart = _to_utc(start_time)
    if not dtstart:
        return ""
    dtend = _to_utc(end_time) or dtstart.replace(hour=min(dtstart.hour + 2, 23))
    dtstamp = datetime.now(timezone.utc)

    status_line = "STATUS:CANCELLED" if method == "CANCEL" else "STATUS:CONFIRMED"

    lines = [
        "BEGIN:VCALENDAR",
        "PRODID:-//Calyx//Group Events//EN",
        "VERSION:2.0",
        "CALSCALE:GREGORIAN",
        f"METHOD:{method}",
        "BEGIN:VEVENT",
        f"UID:{event_uid}",
        f"DTSTAMP:{_fmt_utc(dtstamp)}",
        f"DTSTART:{_fmt_utc(dtstart)}",
        f"DTEND:{_fmt_utc(dtend)}",
        f"SEQUENCE:{int(sequence or 0)}",
        status_line,
        f"TRANSP:{'TRANSPARENT' if method == 'CANCEL' else 'OPAQUE'}",
        _fold(f"SUMMARY:{_esc(title)}"),
        _fold(f"ORGANIZER;CN={_esc(organizer_name)}:mailto:{organizer_email}"),
        _fold(
            f"ATTENDEE;CN={_esc(attendee_name or attendee_email)};"
            f"ROLE=REQ-PARTICIPANT;PARTSTAT={attendee_partstat};RSVP=TRUE:"
            f"mailto:{attendee_email}"
        ),
    ]
    if location:
        lines.append(_fold(f"LOCATION:{_esc(location)}"))
    if description:
        lines.append(_fold(f"DESCRIPTION:{_esc(description)}"))
    if url:
        lines.append(_fold(f"URL:{url}"))
    lines.extend(["END:VEVENT", "END:VCALENDAR"])
    return "\r\n".join(lines) + "\r\n"


def send_calendar_invite(
    *,
    settings: Settings,
    to_email: str,
    to_name: str,
    subject: str,
    html_body: str,
    ics_content: str,
    method: str = "REQUEST",
) -> None:
    """Send an email containing both a human-readable body and an iCalendar invite.

    The text/calendar part lives inside multipart/alternative — Gmail/Apple Mail
    parse the calendar block and offer (or auto-add) the event to the user's
    primary calendar within seconds of delivery.
    """
    outer = MIMEMultipart("mixed")
    outer["Subject"] = subject
    outer["From"] = f"Calyx <{settings.email_from}>"
    outer["To"] = f"{to_name} <{to_email}>" if to_name else to_email

    alt = MIMEMultipart("alternative")
    alt.attach(MIMEText("This invitation is best viewed in an HTML-capable mail client.",
                        "plain", "utf-8"))
    alt.attach(MIMEText(html_body, "html", "utf-8"))

    cal_part = MIMEText(ics_content, "calendar", "utf-8")
    cal_part.replace_header("Content-Type",
                            f'text/calendar; charset=UTF-8; method={method}; component=VEVENT')
    alt.attach(cal_part)

    outer.attach(alt)

    # Also include as a real attachment for clients that need it (some Outlook variants).
    ics_attach = MIMEText(ics_content, "calendar", "utf-8")
    ics_attach.replace_header("Content-Type",
                              f'text/calendar; charset=UTF-8; method={method}; name="invite.ics"')
    ics_attach.add_header("Content-Disposition", 'attachment; filename="invite.ics"')
    outer.attach(ics_attach)

    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(settings.smtp_user, settings.smtp_password)
            server.sendmail(settings.email_from, [to_email], outer.as_string())
        logger.info("Calendar invite (%s) sent to %s for '%s'", method, to_email, subject)
    except Exception:
        logger.exception("Failed to send calendar invite to %s", to_email)


def event_uid_for(event_id: str, settings: Settings) -> str:
    """Stable UID for a calyx group event across create/update/cancel."""
    host = settings.dashboard_url.replace("https://", "").replace("http://", "").split("/")[0] or "calyx.local"
    return f"{event_id}@{host}"


def send_event_invites_to_members(
    *,
    settings: Settings,
    event_id: str,
    title: str,
    start_time: str | None,
    end_time: str | None,
    location: str,
    description: str,
    url: str,
    members: list[dict],
    organizer_user: dict,
    accepted_user_ids: set[int],
    method: str = "REQUEST",
    sequence: int = 0,
    group_name: str = "",
) -> None:
    """Fan out one calendar invite per group member.

    accepted_user_ids: users whose PARTSTAT should be ACCEPTED (e.g. the creator,
    or current 'going' RSVPs on an update).
    """
    organizer_email = settings.email_from
    organizer_name = (organizer_user.get("name") or organizer_user.get("email", "")) + " via Calyx"
    when_str = ""
    try:
        if start_time:
            d = datetime.fromisoformat(start_time)
            when_str = d.strftime("%a %b %-d, %-I:%M %p")
    except (ValueError, TypeError):
        pass

    verb = "added" if method == "REQUEST" and sequence == 0 else ("updated" if method == "REQUEST" else "cancelled")
    subject_prefix = "" if method == "REQUEST" else "Cancelled: "
    subject = f"{subject_prefix}{title}" + (f" — {when_str}" if when_str else "")
    if group_name and method != "CANCEL":
        subject = f"{title} — {group_name}"

    desc_parts = []
    if group_name:
        desc_parts.append(f"From {group_name} on Calyx")
    host_line = f"Added by {organizer_user.get('name') or organizer_user.get('email', '')}"
    if organizer_user.get("phone"):
        host_line += f" · {organizer_user['phone']}"
    desc_parts.append(host_line)
    if description:
        desc_parts.append(description)
    full_desc = "\n\n".join(desc_parts)

    html = f"""<div style="font-family:-apple-system,sans-serif;max-width:520px;margin:0 auto;padding:24px;">
      <h2 style="color:#4a6741;margin:0 0 8px;">{title}</h2>
      <p style="color:#6b7280;margin:0 0 16px;">{when_str}{(' · ' + location) if location else ''}</p>
      <p style="color:#374151;">{organizer_user.get('name') or 'A group-mate'} {verb} this event in <strong>{group_name or 'your group'}</strong>.</p>
      {('<p><a href="' + url + '">' + url + '</a></p>') if url else ''}
      <p style="color:#9ca3af;font-size:12px;margin-top:20px;">This email contains a calendar invite. Your calendar should add it automatically.</p>
    </div>"""

    uid = event_uid_for(event_id, settings)

    for m in members:
        email = m.get("email") or ""
        if not email:
            continue
        partstat = "ACCEPTED" if m["id"] in accepted_user_ids else "NEEDS-ACTION"
        if method == "CANCEL":
            partstat = "DECLINED"
        ics = build_invite_ics(
            event_uid=uid,
            title=title,
            start_time=start_time,
            end_time=end_time,
            location=location,
            description=full_desc,
            url=url,
            organizer_email=organizer_email,
            organizer_name=organizer_name,
            attendee_email=email,
            attendee_name=m.get("name") or "",
            attendee_partstat=partstat,
            method=method,
            sequence=sequence,
        )
        if not ics:
            continue
        try:
            send_calendar_invite(
                settings=settings, to_email=email, to_name=m.get("name") or "",
                subject=subject, html_body=html, ics_content=ics, method=method,
            )
        except Exception:
            logger.exception("Failed to send invite to %s for event %s", email, event_id)
