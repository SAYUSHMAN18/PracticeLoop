"""send_email() backend routing, and the operator's email-status readout.

console (the suite default) logs and returns False; resend does one HTTPS
POST; smtp goes through smtplib. A delivery failure is swallowed, never
raised -- the flow that asked for the mail must not 500.
"""

from __future__ import annotations

import httpx
import pytest

from app.admin.service import email_status
from app.core import email
from app.core.config import settings


async def test_console_backend_logs_and_reports_not_sent(monkeypatch):
    monkeypatch.setattr(settings, "email_backend", "console")
    assert await email.send_email("a@b.com", "s", "body") is False


async def test_resend_backend_posts_to_the_api(monkeypatch):
    monkeypatch.setattr(settings, "email_backend", "resend")
    monkeypatch.setattr(settings, "resend_api_key", "re_test")
    monkeypatch.setattr(settings, "email_from", "PracticeLoop <hi@pl.test>")

    captured = {}

    class _Resp:
        def raise_for_status(self):
            pass

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, headers=None, json=None):
            captured.update(url=url, headers=headers, json=json)
            return _Resp()

    monkeypatch.setattr(httpx, "AsyncClient", _Client)

    assert await email.send_email("s@t.test", "Reset", "click here", html="<b>click</b>") is True
    assert captured["url"] == "https://api.resend.com/emails"
    assert captured["headers"]["Authorization"] == "Bearer re_test"
    assert captured["json"]["from"] == "PracticeLoop <hi@pl.test>"
    assert captured["json"]["to"] == ["s@t.test"]
    assert captured["json"]["html"] == "<b>click</b>"


async def test_a_resend_failure_is_swallowed(monkeypatch):
    monkeypatch.setattr(settings, "email_backend", "resend")
    monkeypatch.setattr(settings, "resend_api_key", "re_test")

    async def boom(*a, **k):
        raise RuntimeError("resend 500")

    monkeypatch.setattr(email, "_send_resend", boom)
    assert await email.send_email("s@t.test", "x", "y") is False  # no raise


@pytest.mark.parametrize(
    "backend,extra,expect_delivering",
    [
        ("console", {}, False),
        ("resend", {"resend_api_key": "re_x"}, True),
        ("resend", {"resend_api_key": ""}, False),
        ("smtp", {"smtp_host": "smtp.test"}, True),
        ("smtp", {"smtp_host": ""}, False),
    ],
)
def test_email_status_reflects_config(monkeypatch, backend, extra, expect_delivering):
    monkeypatch.setattr(settings, "email_backend", backend)
    for k, v in extra.items():
        monkeypatch.setattr(settings, k, v)
    assert email_status()["delivering"] is expect_delivering
