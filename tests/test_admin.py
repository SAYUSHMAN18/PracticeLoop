"""The operator dashboard.

Read-only, and gated by ADMIN_EMAILS -- not a DB role, so there's no
"make admin" path to get wrong and a clone with the var unset has no
admin at all. These tests pin the gate and that the page renders real
aggregates.
"""

from __future__ import annotations

import pytest

from app.core.config import settings
from app.core.db import get_pool
from tests.conftest import signup


@pytest.fixture
def as_admin(monkeypatch):
    monkeypatch.setattr(settings, "admin_emails", "boss@example.com, other@example.com")


async def test_non_admin_is_bounced_to_login(client, as_admin):
    await signup(client, "regular@example.com")
    r = await client.get("/admin", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/login"


async def test_anonymous_is_bounced_to_login(client, as_admin):
    r = await client.get("/admin", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/login"


async def test_with_no_admin_emails_configured_nobody_gets_in(client, monkeypatch):
    monkeypatch.setattr(settings, "admin_emails", "")
    await signup(client, "boss@example.com")
    r = await client.get("/admin", follow_redirects=False)
    assert r.status_code == 303


async def test_admin_sees_the_dashboard_with_real_numbers(client, as_admin):
    # learner first, boss last -- each signup logs that user in, so the
    # active session must end up as the admin.
    await signup(client, "learner@example.com")
    await signup(client, "boss@example.com", name="Boss")

    r = await client.get("/admin")
    assert r.status_code == 200
    assert "Operator dashboard" in r.text
    assert "learner@example.com" in r.text  # recent signups table
    # Two users exist now; the overview counts them.
    pool = await get_pool()
    total = await pool.fetchval("SELECT count(*) FROM users")
    assert str(total) in r.text
