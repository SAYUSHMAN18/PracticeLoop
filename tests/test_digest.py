"""The re-engagement digest.

For each verified, opted-in user it checks -- in the user's timezone --
whether cards are due today and they haven't practiced yet, and if so
sends one reminder. Dedups on last_digest_at. The audit called this the
single most important retention mechanism in the category; the app had no
way to send it before Wave 4's email.
"""

from __future__ import annotations

import pytest

from app.auth.service import create_user
from app.core.config import settings
from app.core.db import get_pool
from app.digest import service
from app.practice.service import create_question, record_attempt
from tests.conftest import signup


@pytest.fixture
def captured_email(monkeypatch):
    sent: list[dict] = []

    async def fake_send(to, subject, text, html=None):
        sent.append({"to": to, "subject": subject, "text": text})
        return True

    monkeypatch.setattr("app.digest.service.send_email", fake_send)
    return sent


async def _verified_user_with_due_card(pool, email: str):
    user_id = await create_user(pool, email, "testpassword123", "Test")
    await pool.execute("UPDATE users SET email_verified_at = now() WHERE user_id = $1", user_id)
    # A fresh question is due immediately (card_states.due IS NULL).
    await create_question(pool, user_id, {"question": f"Q for {email}", "topic": "t"})
    return user_id


async def test_a_verified_user_with_due_cards_gets_one_email(captured_email):
    pool = await get_pool()
    await _verified_user_with_due_card(pool, "due@example.com")

    result = await service.run_digest(pool)
    assert result["sent"] == 1
    assert captured_email[0]["to"] == "due@example.com"
    assert "due" in captured_email[0]["subject"].lower()


async def test_an_unverified_user_is_never_emailed(captured_email):
    pool = await get_pool()
    user_id = await create_user(pool, "unverified@example.com", "testpassword123", "Test")
    await create_question(pool, user_id, {"question": "Q", "topic": "t"})

    result = await service.run_digest(pool)
    assert result["sent"] == 0
    assert captured_email == []


async def test_opting_out_skips_the_user(captured_email):
    pool = await get_pool()
    user_id = await _verified_user_with_due_card(pool, "optout@example.com")
    await pool.execute("UPDATE profiles SET digest_opt_out = true WHERE user_id = $1", user_id)

    assert (await service.run_digest(pool))["sent"] == 0


async def test_having_practiced_today_skips_the_user(captured_email):
    pool = await get_pool()
    user_id = await _verified_user_with_due_card(pool, "active@example.com")
    qid = await create_question(pool, user_id, {"question": "Q2", "topic": "t"})
    await record_attempt(pool, user_id, qid, rating=4)  # practiced now = today

    result = await service.run_digest(pool)
    assert result["sent"] == 0
    assert result["skipped_practiced"] == 1


async def test_no_due_cards_skips_the_user(captured_email):
    pool = await get_pool()
    user_id = await create_user(pool, "nodue@example.com", "testpassword123", "Test")
    await pool.execute("UPDATE users SET email_verified_at = now() WHERE user_id = $1", user_id)
    # no questions at all

    result = await service.run_digest(pool)
    assert result["sent"] == 0
    assert result["skipped_no_due"] == 1


async def test_a_second_run_does_not_re_send(captured_email):
    pool = await get_pool()
    await _verified_user_with_due_card(pool, "dedup@example.com")

    assert (await service.run_digest(pool))["sent"] == 1
    assert (await service.run_digest(pool))["sent"] == 0  # last_digest_at guards it


async def test_unsubscribe_link_opts_the_user_out(client):
    await signup(client, "unsub@example.com")
    pool = await get_pool()
    user_id = await pool.fetchval("SELECT user_id FROM users WHERE email = $1", "unsub@example.com")

    token = service.unsubscribe_token(user_id)
    r = await client.get(f"/digest/unsubscribe?token={token}")
    assert r.status_code == 200
    assert "reminders are off" in r.text

    opted_out = await pool.fetchval("SELECT digest_opt_out FROM profiles WHERE user_id = $1", user_id)
    assert opted_out is True


async def test_a_tampered_unsubscribe_token_changes_nothing(client):
    r = await client.get("/digest/unsubscribe?token=not-a-real-token")
    assert r.status_code == 200  # same page, no leak


async def test_cron_endpoint_requires_the_token(client, monkeypatch):
    monkeypatch.setattr(settings, "digest_cron_token", "s3cret")
    assert (await client.post("/cron/digest")).status_code == 401
    assert (await client.post("/cron/digest", headers={"Authorization": "Bearer s3cret"})).status_code == 200


async def test_cron_endpoint_fails_closed_when_unconfigured(client, monkeypatch):
    monkeypatch.setattr(settings, "digest_cron_token", "")
    assert (await client.post("/cron/digest", headers={"Authorization": "Bearer x"})).status_code == 503
