"""Transactional email -- one function, three backends.

`console` (the default, and what the test suite uses): the message is
logged and nothing leaves the process. `resend`: one HTTPS POST to the
Resend API -- set RESEND_API_KEY and EMAIL_FROM and you're done, no SMTP
host/port/TLS to get wrong. `smtp`: a real send through stdlib smtplib
(Gmail, SES, Postmark, Mailgun, a local relay -- all speak SMTP), run off
the event loop.

A deploy that wants password-reset and digest email to actually arrive
sets EMAIL_BACKEND to `resend` or `smtp` and the matching vars; otherwise
send_email() logs and returns, and every flow still works end to end, it
just never delivers.
"""

from __future__ import annotations

import smtplib
from email.message import EmailMessage

import httpx
from starlette.concurrency import run_in_threadpool

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class EmailNotConfigured(RuntimeError):
    """EMAIL_BACKEND=smtp but the SMTP_* settings are incomplete."""


def _from_address() -> str:
    return settings.email_from or settings.smtp_user or "no-reply@practiceloop.local"


def _build(to: str, subject: str, text: str, html: str | None) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = _from_address()
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


async def _send_resend(to: str, subject: str, text: str, html: str | None) -> None:
    key = settings.resend_api_key.strip()
    if not key:
        raise EmailNotConfigured("EMAIL_BACKEND=resend but RESEND_API_KEY is unset.")
    payload: dict[str, object] = {"from": _from_address(), "to": [to], "subject": subject, "text": text}
    if html:
        payload["html"] = html
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {key}"},
            json=payload,
        )
        resp.raise_for_status()


async def send_email(to: str, subject: str, text: str, html: str | None = None) -> bool:
    """Deliver one message. Returns whether it was actually sent (False for
    the console backend, or on a swallowed delivery failure). Never raises
    for a delivery problem -- a reset email that fails to send must not
    500 the request that asked for it; the user can try again."""
    backend = settings.email_backend.strip().lower()

    if backend == "resend":
        try:
            await _send_resend(to, subject, text, html)
            logger.info("email[resend] sent to=%s subject=%s", to, subject)
            return True
        except Exception:
            logger.exception("email[resend] send failed to=%s subject=%s", to, subject)
            return False

    if backend == "smtp":
        try:
            await run_in_threadpool(_send_smtp_sync, _build(to, subject, text, html))
            logger.info("email[smtp] sent to=%s subject=%s", to, subject)
            return True
        except Exception:
            logger.exception("email[smtp] send failed to=%s subject=%s", to, subject)
            return False

    logger.info("email[console] to=%s subject=%s\n%s", to, subject, text)
    return False
