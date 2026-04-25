import logging

import aiosmtplib
from email.mime.text import MIMEText

from app.core.config import settings

logger = logging.getLogger(__name__)


async def send_email(to: str, subject: str, body: str) -> None:
    """Send a plain-text email. Swallows all errors — email is best-effort."""
    if not settings.SMTP_ENABLED:
        logger.debug("SMTP disabled — skipping email to %s", to)
        return

    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = settings.SMTP_FROM
    msg["To"] = to

    try:
        await aiosmtplib.send(
            msg,
            hostname=settings.SMTP_HOST,
            port=settings.SMTP_PORT,
            username=settings.SMTP_USER or None,
            password=settings.SMTP_PASSWORD.get_secret_value() or None,
            start_tls=settings.SMTP_PORT == 587,
        )
        logger.info("Email sent to %s: %s", to, subject)
    except Exception as exc:
        logger.warning("Email send failed to %s: %s", to, exc)
