"""Signup email verification.

Non-blocking -- the app works unverified -- but the account carries a
flag, a dashboard banner nags until it's set, and the re-engagement
digest only emails verified addresses.
"""

from __future__ import annotations

import pytest

from app.core.db import get_pool
from tests.conftest import signup


@pytest.fixture
def captured_email(monkeypatch):
    sent: list[dict] = []

    async def fake_send(to, subject, text, html=None):
        sent.append({"to": to, "subject": subject, "text": text})
        return True

    monkeypatch.setattr("app.auth.service.send_email", fake_send)
    return sent


def _verify_link(body: str) -> str:
    marker = "verify-email?token="
    start = body.index(marker) + len(marker)
    end = start
    while end < len(body) and body[end] not in "\n \t":
        end += 1
    return body[start:end]


async def test_signup_sends_a_verification_email(client, captured_email):
    await signup(client, "verify-me@example.com")
    assert len(captured_email) == 1
    assert captured_email[0]["to"] == "verify-me@example.com"
    assert "verify-email?token=" in captured_email[0]["text"]

    pool = await get_pool()
    verified = await pool.fetchval(
        "SELECT email_verified_at FROM users WHERE email = $1", "verify-me@example.com"
    )
    assert verified is None  # not yet


async def test_clicking_the_link_verifies_and_signs_in(client, captured_email):
    await signup(client, "clicker@example.com")
    token = _verify_link(captured_email[0]["text"])
    await client.post("/logout")

    r = await client.get(f"/verify-email?token={token}")
    assert r.status_code == 200
    assert "Email confirmed" in r.text

    pool = await get_pool()
    verified = await pool.fetchval(
        "SELECT email_verified_at FROM users WHERE email = $1", "clicker@example.com"
    )
    assert verified is not None
    # signed in by the click
    assert (await client.get("/dashboard")).status_code == 200


async def test_a_used_token_is_rejected(client, captured_email):
    await signup(client, "twice@example.com")
    token = _verify_link(captured_email[0]["text"])
    assert (await client.get(f"/verify-email?token={token}")).status_code == 200
    second = await client.get(f"/verify-email?token={token}")
    assert second.status_code == 400
    assert "isn't valid" in second.text


async def test_dashboard_banner_shows_until_verified(client, captured_email):
    await signup(client, "banner@example.com")
    before = await client.get("/dashboard")
    assert "Confirm your email" in before.text

    token = _verify_link(captured_email[0]["text"])
    await client.get(f"/verify-email?token={token}")

    after = await client.get("/dashboard")
    assert "Confirm your email" not in after.text


async def test_resend_issues_a_fresh_link_and_invalidates_the_old(client, captured_email):
    await signup(client, "resend@example.com")
    first = _verify_link(captured_email[0]["text"])

    r = await client.post("/verify-email/resend", follow_redirects=False)
    assert r.status_code == 303
    assert len(captured_email) == 2
    second = _verify_link(captured_email[1]["text"])
    assert second != first

    # old link is dead, new one works
    assert (await client.get(f"/verify-email?token={first}")).status_code == 400
    assert (await client.get(f"/verify-email?token={second}")).status_code == 200
