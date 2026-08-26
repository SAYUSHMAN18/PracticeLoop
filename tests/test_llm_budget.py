from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.auth.service import get_user_by_email
from app.core.config import settings
from app.core.db import get_pool
from app.core.llm_budget import require_llm_budget
from tests.conftest import signup


async def test_calls_within_budget_are_allowed(client, monkeypatch):
    monkeypatch.setattr(settings, "llm_daily_budget", 3)
    await signup(client, "budget-ok@example.com")

    pool = await get_pool()
    user = await get_user_by_email(pool, "budget-ok@example.com")

    for _ in range(3):
        await require_llm_budget(user_id=user["user_id"], pool=pool)  # must not raise


async def test_the_call_that_exceeds_budget_is_rejected_with_a_clear_message(client, monkeypatch):
    """The count-then-check ordering matters: the call that pushes the
    total *over* the budget is the one rejected, not the one after it --
    otherwise a user gets one free call beyond their stated limit."""
    monkeypatch.setattr(settings, "llm_daily_budget", 2)
    await signup(client, "budget-exceeded@example.com")

    pool = await get_pool()
    user = await get_user_by_email(pool, "budget-exceeded@example.com")

    await require_llm_budget(user_id=user["user_id"], pool=pool)
    await require_llm_budget(user_id=user["user_id"], pool=pool)

    with pytest.raises(HTTPException) as exc_info:
        await require_llm_budget(user_id=user["user_id"], pool=pool)

    assert exc_info.value.status_code == 429
    assert "2 AI generations" in exc_info.value.detail
    assert "midnight" in exc_info.value.detail


async def test_budget_is_tracked_per_user_not_globally(client, monkeypatch):
    monkeypatch.setattr(settings, "llm_daily_budget", 1)
    await signup(client, "budget-a@example.com")

    from httpx import ASGITransport, AsyncClient

    from app.main import app

    other_client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    await signup(other_client, "budget-b@example.com")

    pool = await get_pool()
    user_a = await get_user_by_email(pool, "budget-a@example.com")
    user_b = await get_user_by_email(pool, "budget-b@example.com")

    await require_llm_budget(user_id=user_a["user_id"], pool=pool)  # uses up A's one call
    await require_llm_budget(user_id=user_b["user_id"], pool=pool)  # B is unaffected

    await other_client.aclose()
