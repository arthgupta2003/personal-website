"""Send the digest email via SMTP + STARTTLS."""

from __future__ import annotations

import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from recom.config import Settings

logger = logging.getLogger(__name__)


def send_email(subject: str, html_body: str, settings: Settings) -> None:
    """Send an HTML email with a plain-text fallback.

    Uses SMTP with STARTTLS as configured in *settings*.
    """

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = settings.email_from
    msg["To"] = settings.email_to

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
            server.sendmail(settings.email_from, settings.email_to, msg.as_string())

        logger.info(
            "Email sent successfully to %s via %s:%d",
            settings.email_to,
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
