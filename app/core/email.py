"""Transactional email -- one function, two backends.

`console` (the default, and what the test suite uses): the message is
logged and nothing leaves the process. `smtp`: a real send through
stdlib smtplib, run off the event loop so it never blocks a request.

Nothing here is provider-specific. Point the SMTP_* settings at Gmail,
Amazon SES, Postmark, Resend, Mailgun, Fastmail, or a local relay -- they
all speak SMTP. A production deploy that wants password-reset email to
actually arrive must set EMAIL_BACKEND=smtp and the SMTP_* vars;
otherwise send_email() logs and returns, and the reset flow still works
end to end, it just never delivers.
"""

from __future__ import annotations

import smtplib
from email.message import EmailMessage

from starlette.concurrency import run_in_threadpool

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class EmailNotConfigured(RuntimeError):
    """EMAIL_BACKEND=smtp but the SMTP_* settings are incomplete."""


def _build(to: str, subject: str, text: str, html: str | None) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = settings.email_from or settings.smtp_user or "no-reply@practiceloop.local"
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(text)
    if html:
        msg.add_alternative(html, subtype="html")
    return msg


def _send_smtp_sync(msg: EmailMessage) -> None:
    host = settings.smtp_host.strip()
    if not host:
        raise EmailNotConfigured("EMAIL_BACKEND=smtp but SMTP_HOST is unset.")

    port = settings.smtp_port
    if settings.smtp_use_ssl:
        server: smtplib.SMTP = smtplib.SMTP_SSL(host, port, timeout=15)
    else:
        server = smtplib.SMTP(host, port, timeout=15)
    try:
        if settings.smtp_use_starttls and not settings.smtp_use_ssl:
            server.starttls()
        if settings.smtp_user:
            server.login(settings.smtp_user, settings.smtp_password)
        server.send_message(msg)
    finally:
        server.quit()


async def send_email(to: str, subject: str, text: str, html: str | None = None) -> bool:
    """Deliver one message. Returns whether it was actually sent (False for
    the console backend, or on a swallowed SMTP failure). Never raises for
    a delivery problem -- a reset email that fails to send must not 500 the
    request that asked for it; the user can try again."""
    backend = settings.email_backend.strip().lower()
    msg = _build(to, subject, text, html)

    if backend != "smtp":
        logger.info("email[console] to=%s subject=%s\n%s", to, subject, text)
        return False

    try:
        await run_in_threadpool(_send_smtp_sync, msg)
        logger.info("email[smtp] sent to=%s subject=%s", to, subject)
        return True
    except Exception:
        logger.exception("email[smtp] send failed to=%s subject=%s", to, subject)
        return False
