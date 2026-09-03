"""Account recovery and lockout.

The whole auth surface used to be signup / login / logout: forget your
password and the account was gone for good, and there was nothing between
a bot and an account but a per-IP rate limit. These tests pin the two
additions -- a reset-by-email flow (token hashed at rest, one hour,
single use) and a per-account lockout that any success clears.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.auth import service
from app.core.db import get_pool
from tests.conftest import signup


@pytest.fixture
def captured_email(monkeypatch):
    """Swap the console/SMTP send for an in-memory list. The reset link is
    in the body, which is how the tests below get the token."""
    sent: list[dict] = []

    async def fake_send(to, subject, text, html=None):
        sent.append({"to": to, "subject": subject, "text": text, "html": html})
        return True

    monkeypatch.setattr("app.auth.router.send_email", fake_send)
    return sent


def _token_from(email_body: str) -> str:
    marker = "reset-password?token="
    start = email_body.index(marker) + len(marker)
    end = start
    while end < len(email_body) and email_body[end] not in "\n \t":
        end += 1
    return email_body[start:end]


# ---------- reset flow ----------


async def test_full_reset_flow_signs_the_user_in_with_the_new_password(client, captured_email):
    await signup(client, "reset-me@example.com", password="oldpassword123")

    r = await client.post("/forgot-password", data={"email": "reset-me@example.com"})
    assert r.status_code == 200
    assert "on its way" in r.text
    assert len(captured_email) == 1
    token = _token_from(captured_email[0]["text"])

    form = await client.get(f"/reset-password?token={token}")
    assert form.status_code == 200
    assert "Set a new password" in form.text

    done = await client.post(
        "/reset-password", data={"token": token, "password": "brandnewpass456"}, follow_redirects=False
    )
    assert done.status_code == 303
    assert done.headers["location"] in ("/dashboard", "/welcome")

    # New password works, old one doesn't.
    await client.post("/logout")
    bad = await client.post("/login", data={"email": "reset-me@example.com", "password": "oldpassword123"})
    assert bad.status_code == 400
    good = await client.post(
        "/login",
        data={"email": "reset-me@example.com", "password": "brandnewpass456"},
        follow_redirects=False,
    )
    assert good.status_code == 303


async def test_forgot_password_never_reveals_whether_an_account_exists(client, captured_email):
    r = await client.post("/forgot-password", data={"email": "nobody-here@example.com"})
    assert r.status_code == 200
    assert "on its way" in r.text  # same copy as the real case
    assert captured_email == []  # ... but nothing was sent


async def test_a_used_token_cannot_be_used_again(client, captured_email):
    await signup(client, "reuse@example.com", password="oldpassword123")
    await client.post("/forgot-password", data={"email": "reuse@example.com"})
    token = _token_from(captured_email[0]["text"])

    first = await client.post("/reset-password", data={"token": token, "password": "firstnewpass123"})
    assert first.status_code == 303
    second = await client.post("/reset-password", data={"token": token, "password": "secondnewpass123"})
    assert second.status_code == 400
    assert "invalid or has expired" in second.text


async def test_an_expired_token_is_rejected(client, captured_email):
    await signup(client, "expired@example.com", password="oldpassword123")
    await client.post("/forgot-password", data={"email": "expired@example.com"})
    token = _token_from(captured_email[0]["text"])

    pool = await get_pool()
    await pool.execute(
        "UPDATE password_reset_tokens SET created_at = $1",
        datetime.now(timezone.utc) - timedelta(hours=2),
    )
    r = await client.post("/reset-password", data={"token": token, "password": "toolatenow123"})
    assert r.status_code == 400
    assert "invalid or has expired" in r.text


async def test_setting_a_password_kills_other_pending_reset_tokens(client, captured_email):
    await signup(client, "multi@example.com", password="oldpassword123")
    await client.post("/forgot-password", data={"email": "multi@example.com"})
    await client.post("/forgot-password", data={"email": "multi@example.com"})
    first_token = _token_from(captured_email[0]["text"])
    second_token = _token_from(captured_email[1]["text"])

    ok = await client.post("/reset-password", data={"token": second_token, "password": "thenewone123"})
    assert ok.status_code == 303
    stale = await client.post("/reset-password", data={"token": first_token, "password": "shouldfail123"})
    assert stale.status_code == 400
    assert "invalid or has expired" in stale.text


# ---------- signup email validation ----------


async def test_signup_rejects_a_malformed_email(client):
    r = await client.post(
        "/signup", data={"name": "Bad", "email": "not-an-email", "password": "testpassword123"}
    )
    assert r.status_code == 400
    assert "valid email" in r.text


# ---------- account lockout ----------


async def test_repeated_failures_lock_the_account_then_a_reset_frees_it(client, captured_email):
    await signup(client, "lockme@example.com", password="correcthorse123")
    pool = await get_pool()

    for _ in range(service.settings.login_lockout_threshold):
        await client.post("/login", data={"email": "lockme@example.com", "password": "wrong"})

    locked_until = await pool.fetchval(
        "SELECT locked_until FROM users WHERE email = $1", "lockme@example.com"
    )
    assert locked_until is not None and locked_until > datetime.now(timezone.utc)

    # Even the *right* password is refused while locked, with a distinct message.
    r = await client.post("/login", data={"email": "lockme@example.com", "password": "correcthorse123"})
    assert r.status_code == 400
    assert "too many" in r.text.lower()

    # A password reset clears the lock.
    await client.post("/forgot-password", data={"email": "lockme@example.com"})
    token = _token_from(captured_email[-1]["text"])
    await client.post("/reset-password", data={"token": token, "password": "afreshstart123"})
    cleared = await pool.fetchrow(
        "SELECT failed_login_count, locked_until FROM users WHERE email = $1", "lockme@example.com"
    )
    assert cleared["failed_login_count"] == 0
    assert cleared["locked_until"] is None


async def test_a_successful_login_resets_the_failure_count(client):
    await signup(client, "recover@example.com", password="correcthorse123")
    pool = await get_pool()

    for _ in range(3):
        await client.post("/login", data={"email": "recover@example.com", "password": "wrong"})
    assert (
        await pool.fetchval("SELECT failed_login_count FROM users WHERE email = $1", "recover@example.com")
        == 3
    )

    ok = await client.post(
        "/login",
        data={"email": "recover@example.com", "password": "correcthorse123"},
        follow_redirects=False,
    )
    assert ok.status_code == 303
    assert (
        await pool.fetchval("SELECT failed_login_count FROM users WHERE email = $1", "recover@example.com")
        == 0
    )
