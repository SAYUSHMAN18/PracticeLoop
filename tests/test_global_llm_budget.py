"""Deployment-wide LLM ceiling.

The per-user budget bounds one account. With open signup, nothing bounded
the total: N accounts x LLM_DAILY_BUDGET against one shared provider key is
unbounded spend, and exhausting the key degrades every user at once rather
than just the account doing it.
"""

from __future__ import annotations

import pytest

from app.auth.service import create_user
from app.core.config import settings
from app.core.db import get_pool
from app.core.llm_budget import (
    LLMBudgetExceeded,
    LLMGlobalBudgetExceeded,
    consume_llm_budget,
)


@pytest.fixture(autouse=True)
async def _reset_global_counter():
    pool = await get_pool()
    await pool.execute("TRUNCATE llm_usage_global")
    original = settings.llm_global_daily_budget
    yield
    settings.llm_global_daily_budget = original
    await pool.execute("TRUNCATE llm_usage_global")


async def test_zero_means_no_global_ceiling(monkeypatch):
    """The default, and the right one for a single-user install where the
    per-user cap already is the whole story."""
    pool = await get_pool()
    settings.llm_global_daily_budget = 0
    monkeypatch.setattr(settings, "llm_daily_budget", 1000)

    user_id = await create_user(pool, "global-off@example.com", "testpassword123", "Test")
    for _ in range(25):
        await consume_llm_budget(pool, user_id)

    assert await pool.fetchval("SELECT count(*) FROM llm_usage_global") == 0


async def test_the_ceiling_applies_across_different_users(monkeypatch):
    """The point of the whole feature: one user must not be able to spend
    the shared budget, and the next user must feel it."""
    pool = await get_pool()
    settings.llm_global_daily_budget = 3
    monkeypatch.setattr(settings, "llm_daily_budget", 1000)

    first = await create_user(pool, "global-a@example.com", "testpassword123", "A")
    second = await create_user(pool, "global-b@example.com", "testpassword123", "B")

    await consume_llm_budget(pool, first)
    await consume_llm_budget(pool, first)
    await consume_llm_budget(pool, second)

    with pytest.raises(LLMGlobalBudgetExceeded) as exc:
        await consume_llm_budget(pool, second)
    assert exc.value.status_code == 429
    assert "deployment" in exc.value.detail


async def test_a_user_over_their_own_budget_does_not_burn_shared_credit(monkeypatch):
    """The per-user check runs first, so being rejected personally costs the
    shared pool nothing -- otherwise one user hammering a route they're
    already locked out of would drain everyone else's budget."""
    pool = await get_pool()
    settings.llm_global_daily_budget = 100
    monkeypatch.setattr(settings, "llm_daily_budget", 2)

    user_id = await create_user(pool, "global-c@example.com", "testpassword123", "C")
    await consume_llm_budget(pool, user_id)
    await consume_llm_budget(pool, user_id)

    for _ in range(5):
        with pytest.raises(LLMBudgetExceeded):
            await consume_llm_budget(pool, user_id)

    assert await pool.fetchval("SELECT call_count FROM llm_usage_global") == 2


async def test_the_global_count_survives_a_restart(monkeypatch):
    """DB-backed, not in-memory: a free-tier spin-down or redeploy must not
    hand everyone a fresh budget."""
    pool = await get_pool()
    settings.llm_global_daily_budget = 5
    monkeypatch.setattr(settings, "llm_daily_budget", 1000)

    user_id = await create_user(pool, "global-d@example.com", "testpassword123", "D")
    await consume_llm_budget(pool, user_id)
    await consume_llm_budget(pool, user_id)

    # Nothing cached in the process -- the count is read back from Postgres.
    assert await pool.fetchval("SELECT call_count FROM llm_usage_global") == 2
