"""Read-only operator view.

The operator had no way to see the shape of the deployment -- how many
users, whether signups are happening, what the AI layer is costing, or
whether calls are failing. This is a single page of aggregates, all
straight reads, no new tables. It reads llm_calls (migration 0024) for
the cost and reliability numbers and the standard tables for the rest.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

import asyncpg


async def overview(pool: asyncpg.Pool) -> dict:
    keys = (
        "users_total",
        "users_7d",
        "users_24h",
        "questions_total",
        "attempts_total",
        "attempts_24h",
        "paths_total",
    )
    values = await asyncio.gather(
        pool.fetchval("SELECT count(*) FROM users"),
        pool.fetchval("SELECT count(*) FROM users WHERE created_at > now() - interval '7 days'"),
        pool.fetchval("SELECT count(*) FROM users WHERE created_at > now() - interval '24 hours'"),
        pool.fetchval("SELECT count(*) FROM questions"),
        pool.fetchval("SELECT count(*) FROM attempts"),
        pool.fetchval("SELECT count(*) FROM attempts WHERE practiced_at > now() - interval '24 hours'"),
        pool.fetchval("SELECT count(*) FROM learning_paths"),
    )
    return dict(zip(keys, values, strict=True))


_WINDOW_SQL = """
    SELECT count(*) AS calls,
           count(*) FILTER (WHERE cached) AS cached,
           count(*) FILTER (WHERE failed) AS failed,
           coalesce(sum(prompt_tokens), 0) AS prompt_tokens,
           coalesce(sum(completion_tokens), 0) AS completion_tokens
    FROM llm_calls WHERE created_at > now() - $1::interval
"""


def _shape(r: asyncpg.Record) -> dict:
    calls = r["calls"] or 0
    return {
        "calls": calls,
        "cached": r["cached"] or 0,
        "failed": r["failed"] or 0,
        "cache_hit_pct": round(100 * (r["cached"] or 0) / calls) if calls else 0,
        "fail_pct": round(100 * (r["failed"] or 0) / calls) if calls else 0,
        "prompt_tokens": r["prompt_tokens"] or 0,
        "completion_tokens": r["completion_tokens"] or 0,
    }


async def llm_stats(pool: asyncpg.Pool) -> dict:
    """Cost and reliability from llm_calls, over 24h and 7d. Token counts
    are best-effort (the provider has to report them). Dollar figures are
    deliberately not shown -- they'd need a per-model price table that goes
    stale; tokens and call counts are the honest numbers."""
    day, week, recent, by_provider = await asyncio.gather(
        pool.fetchrow(_WINDOW_SQL, timedelta(hours=24)),
        pool.fetchrow(_WINDOW_SQL, timedelta(days=7)),
        pool.fetch(
            """SELECT provider, model, failed, cached, latency_ms, created_at
               FROM llm_calls ORDER BY call_id DESC LIMIT 15"""
        ),
        pool.fetch(
            """SELECT provider, count(*) AS calls, count(*) FILTER (WHERE failed) AS failed
               FROM llm_calls WHERE created_at > now() - interval '7 days'
               GROUP BY provider ORDER BY calls DESC"""
        ),
    )
    return {
        "day": _shape(day),
        "week": _shape(week),
        "recent": [dict(r) for r in recent],
        "by_provider": [dict(r) for r in by_provider],
    }


def email_status() -> dict:
    """What the operator needs to know at a glance: is email actually
    being delivered, and is the digest cron reachable."""
    from app.core.config import settings

    backend = settings.email_backend.strip().lower()
    if backend == "resend":
        delivering = bool(settings.resend_api_key.strip())
        detail = "Resend" + ("" if delivering else " -- RESEND_API_KEY not set")
    elif backend == "smtp":
        delivering = bool(settings.smtp_host.strip())
        detail = f"SMTP {settings.smtp_host}" if delivering else "SMTP -- SMTP_HOST not set"
    else:
        delivering = False
        detail = "console (logs only, nothing sent)"
    return {
        "delivering": delivering,
        "detail": detail,
        "from": settings.email_from or "(unset)",
        "digest_cron": "configured" if settings.digest_cron_token.strip() else "not configured (503)",
    }


async def recent_signups(pool: asyncpg.Pool, limit: int = 20) -> list[asyncpg.Record]:
    return await pool.fetch(
        """SELECT u.email, u.name, u.role, u.created_at,
                  (SELECT count(*) FROM attempts a WHERE a.user_id = u.user_id) AS attempts
           FROM users u ORDER BY u.created_at DESC LIMIT $1""",
        limit,
    )
