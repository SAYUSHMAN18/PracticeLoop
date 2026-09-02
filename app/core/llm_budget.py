from __future__ import annotations

from datetime import date

import asyncpg
from fastapi import Depends, HTTPException

from app.core.config import settings
from app.core.db import get_pool
from app.core.deps import require_user_id


async def _increment_and_check(pool: asyncpg.Pool, user_id: int) -> int:
    return await pool.fetchval(
        """INSERT INTO llm_usage (user_id, usage_date, call_count)
           VALUES ($1, $2, 1)
           ON CONFLICT (user_id, usage_date)
           DO UPDATE SET call_count = llm_usage.call_count + 1
           RETURNING call_count""",
        user_id,
        date.today(),
    )


async def _increment_and_check_global(pool: asyncpg.Pool) -> int:
    return await pool.fetchval(
        """INSERT INTO llm_usage_global (usage_date, call_count)
           VALUES ($1, 1)
           ON CONFLICT (usage_date)
           DO UPDATE SET call_count = llm_usage_global.call_count + 1
           RETURNING call_count""",
        date.today(),
    )


class LLMBudgetExceeded(HTTPException):
    def __init__(self) -> None:
        super().__init__(
            status_code=429,
            detail=(
                f"You've used all {settings.llm_daily_budget} AI generations for today. "
                "It resets at midnight UTC."
            ),
        )


class LLMGlobalBudgetExceeded(HTTPException):
    """Distinct from LLMBudgetExceeded on purpose: "you personally are out
    until midnight" and "this deployment is out for everyone" are different
    facts, and telling a student the first when the second is true sends
    them off to wait for a reset that isn't theirs to wait for."""

    def __init__(self) -> None:
        super().__init__(
            status_code=429,
            detail=(
                "This deployment has used all its shared AI generations for today. "
                "Everything that works without AI still works -- try again after midnight UTC."
            ),
        )


async def consume_llm_budget(pool: asyncpg.Pool, user_id: int) -> None:
    """The actual check, callable directly for a route that may issue
    *several* LLM calls in one request (e.g. generating a question per gap
    in a deck) -- each call needs its own budget check, not one check for
    the whole request, or a five-skill batch would only ever cost 1 against
    the daily count.

    DB-backed and per-user, not in-memory/per-IP like RateLimitMiddleware --
    an in-memory counter would reset on every redeploy or free-tier
    spin-down and let a user just wait it out, and IP-based limiting is
    weak for an authenticated multi-device user.

    Enforces two ceilings: the per-user daily budget, and (when set) a
    deployment-wide one -- open signup means N users x the per-user budget
    is otherwise unbounded spend on one shared provider key.

    Counts the call *before* checking the limit (so a request that pushes
    the count over the budget is itself rejected, not the next one after
    it), and fails closed with a clear 429 rather than a raw 500 or -- worse
    -- silently letting an unbounded number of LLM calls through.
    """
    count = await _increment_and_check(pool, user_id)
    if count > settings.llm_daily_budget:
        raise LLMBudgetExceeded()

    # Counted after the per-user check, so a user who is already over their
    # own budget doesn't also burn a slot from the shared pool on the way to
    # being rejected. 0 means "no global ceiling" -- the default, and the
    # right one for a single-user install where the per-user cap is already
    # the whole story.
    if settings.llm_global_daily_budget > 0:
        global_count = await _increment_and_check_global(pool)
        if global_count > settings.llm_global_daily_budget:
            raise LLMGlobalBudgetExceeded()


async def require_llm_budget(
    user_id: int = Depends(require_user_id),
    pool: asyncpg.Pool = Depends(get_pool),
) -> None:
    """FastAPI dependency for a route that makes exactly one LLM call
    (capture structuring, single study-card generation, gap analysis)."""
    await consume_llm_budget(pool, user_id)
