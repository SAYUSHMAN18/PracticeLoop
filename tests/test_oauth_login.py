"""Google Sign-In: a plain OAuth 2.0 authorization-code exchange
(app/auth/oauth.py), driving app/auth/service.py's get_or_create_oauth_user
and the /auth/google + /auth/google/callback routes. GOOGLE_OAUTH_CLIENT_ID
is unset in tests -- the real "not configured" state, same as every other
optional integration's tests -- so oauth.exchange_code_for_userinfo is
monkeypatched to exercise the callback without ever calling Google.
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

from app.auth import oauth, service
from app.core.config import settings
from app.core.db import get_pool
from tests.conftest import signup


def _configure_oauth(monkeypatch) -> None:
    monkeypatch.setattr(settings, "google_oauth_client_id", "test-client-id")
    monkeypatch.setattr(settings, "google_oauth_client_secret", "test-client-secret")


async def _start_oauth(client) -> str:
    """Drives /auth/google and returns the state it stashed in the
    session -- pulled from the real redirect URL, not guessed, since it's
    randomly generated per attempt."""
    start = await client.get("/auth/google", follow_redirects=False)
    assert start.status_code == 303
    location = start.headers["location"]
    assert location.startswith("https://accounts.google.com/")
    state = parse_qs(urlparse(location).query)["state"][0]
    assert state
    return state


# ---------- the button ----------


async def test_google_button_hidden_when_not_configured(client):
    assert "Continue with Google" not in (await client.get("/login")).text
    assert "Continue with Google" not in (await client.get("/signup")).text


async def test_google_button_shown_when_configured(client, monkeypatch):
    _configure_oauth(monkeypatch)
    assert "Continue with Google" in (await client.get("/login")).text
    assert "Continue with Google" in (await client.get("/signup")).text


async def test_starting_oauth_without_config_just_redirects_home(client):
    response = await client.get("/auth/google", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


# ---------- the callback ----------


async def test_callback_creates_a_new_user_and_logs_them_in(client, monkeypatch):
    _configure_oauth(monkeypatch)

    async def fake_exchange(code: str) -> dict:
        assert code == "the-auth-code"
        return {"email": "new-via-google@example.com", "name": "Googly Student"}

    monkeypatch.setattr(oauth, "exchange_code_for_userinfo", fake_exchange)

    state = await _start_oauth(client)
    callback = await client.get(
        "/auth/google/callback", params={"code": "the-auth-code", "state": state}, follow_redirects=False
    )
    assert callback.status_code == 303
    assert callback.headers["location"] == "/welcome"  # a brand-new account still onboards

    pool = await get_pool()
    row = await pool.fetchrow(
        "SELECT password_hash, oauth_provider, email_verified_at, name FROM users WHERE email = $1",
        "new-via-google@example.com",
    )
    assert row["password_hash"] is None
    assert row["oauth_provider"] == "google"
    assert row["email_verified_at"] is not None  # Google already verified it
    assert row["name"] == "Googly Student"

    # and the session actually took -- an authenticated page loads, not a
    # redirect back to /login
    dashboard = await client.get("/welcome")
    assert dashboard.status_code == 200


async def test_callback_logs_into_an_existing_password_account_by_email(client, monkeypatch):
    """Google has already verified the address, so matching an existing
    password account by email is the same person signing in a second way,
    not a takeover -- and it must not create a duplicate account."""
    await signup(client, "both-methods@example.com")
    await client.post("/welcome/skip")  # so this is genuinely an "already onboarded" login, not day one
    pool = await get_pool()
    original_user_id = await pool.fetchval(
        "SELECT user_id FROM users WHERE email = $1", "both-methods@example.com"
    )
    await client.post("/logout")

    _configure_oauth(monkeypatch)

    async def fake_exchange(code: str) -> dict:
        return {"email": "both-methods@example.com", "name": "Ignored On Existing Account"}

    monkeypatch.setattr(oauth, "exchange_code_for_userinfo", fake_exchange)

    state = await _start_oauth(client)
    callback = await client.get(
        "/auth/google/callback", params={"code": "abc", "state": state}, follow_redirects=False
    )
    assert callback.status_code == 303
    assert callback.headers["location"] == "/dashboard"  # already onboarded from the original signup

    count = await pool.fetchval("SELECT count(*) FROM users WHERE email = $1", "both-methods@example.com")
    assert count == 1
    row = await pool.fetchrow(
        "SELECT user_id, oauth_provider FROM users WHERE email = $1", "both-methods@example.com"
    )
    assert row["user_id"] == original_user_id
    assert row["oauth_provider"] == "google"  # now linked


async def test_callback_rejects_a_mismatched_state(client, monkeypatch):
    _configure_oauth(monkeypatch)
    await _start_oauth(client)  # sets a real state in the session

    response = await client.get(
        "/auth/google/callback", params={"code": "abc", "state": "not-the-real-state"}
    )
    assert response.status_code == 400
    # Jinja autoescapes the apostrophe in "didn't", so match around it.
    assert "try again" in response.text


async def test_callback_with_no_prior_start_is_rejected(client, monkeypatch):
    _configure_oauth(monkeypatch)
    response = await client.get("/auth/google/callback", params={"code": "abc", "state": "anything"})
    assert response.status_code == 400


async def test_callback_surfaces_a_failed_exchange(client, monkeypatch):
    _configure_oauth(monkeypatch)

    async def failing_exchange(code: str) -> dict:
        raise oauth.OAuthExchangeFailed("Google rejected the sign-in code.")

    monkeypatch.setattr(oauth, "exchange_code_for_userinfo", failing_exchange)

    state = await _start_oauth(client)
    response = await client.get("/auth/google/callback", params={"code": "abc", "state": state})
    assert response.status_code == 400
    assert "Google rejected the sign-in code." in response.text


async def test_google_denying_consent_is_handled_not_500d(client, monkeypatch):
    _configure_oauth(monkeypatch)
    state = await _start_oauth(client)
    response = await client.get("/auth/google/callback", params={"error": "access_denied", "state": state})
    assert response.status_code == 400


# ---------- password login on an OAuth-only account ----------


async def test_password_login_on_an_oauth_only_account_names_the_provider(client):
    pool = await get_pool()
    await service.get_or_create_oauth_user(
        pool, email="google-only@example.com", name="Google Only", provider="google"
    )

    response = await client.post(
        "/login", data={"email": "google-only@example.com", "password": "whatever-guess"}
    )
    assert response.status_code == 400
    assert "signs in with Google" in response.text
    assert "Incorrect email or password" not in response.text
