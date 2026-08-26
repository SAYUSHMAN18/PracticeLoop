from __future__ import annotations

from datetime import date, timedelta

import asyncpg

_STALE_AFTER_DAYS = 14


class ApplicationNotFound(Exception):
    pass


async def create_application(
    pool: asyncpg.Pool,
    user_id: int,
    company: str,
    role: str,
    listing_id: int | None = None,
    fit_score: int | None = None,
    follow_up_at: date | None = None,
) -> int:
    return await pool.fetchval(
        """INSERT INTO applications (user_id, company, role, listing_id, fit_score, follow_up_at)
           VALUES ($1, $2, $3, $4, $5, $6)
           RETURNING application_id""",
        user_id,
        company,
        role,
        listing_id,
        fit_score,
        follow_up_at,
    )


async def get_application(pool: asyncpg.Pool, user_id: int, application_id: int) -> asyncpg.Record | None:
    return await pool.fetchrow(
        "SELECT * FROM applications WHERE user_id = $1 AND application_id = $2",
        user_id,
        application_id,
    )


async def list_applications(pool: asyncpg.Pool, user_id: int) -> list[asyncpg.Record]:
    return await pool.fetch(
        "SELECT * FROM applications WHERE user_id = $1 ORDER BY applied_at DESC",
        user_id,
    )


async def update_status(
    pool: asyncpg.Pool,
    user_id: int,
    application_id: int,
    status: str,
    interview_at=None,
    notes: str | None = None,
) -> None:
    owned = await get_application(pool, user_id, application_id)
    if owned is None:
        raise ApplicationNotFound(application_id)

    await pool.execute(
        """UPDATE applications
           SET status = $3, interview_at = COALESCE($4, interview_at),
               notes = COALESCE($5, notes)
           WHERE user_id = $1 AND application_id = $2""",
        user_id,
        application_id,
        status,
        interview_at,
        notes,
    )


async def due_follow_ups(pool: asyncpg.Pool, user_id: int, today: date | None = None) -> list[asyncpg.Record]:
    today = today or date.today()
    return await pool.fetch(
        """SELECT * FROM applications
           WHERE user_id = $1 AND follow_up_at IS NOT NULL AND follow_up_at <= $2
             AND status = 'applied'
           ORDER BY follow_up_at""",
        user_id,
        today,
    )


async def stale_applications(
    pool: asyncpg.Pool, user_id: int, today: date | None = None
) -> list[asyncpg.Record]:
    """Applications sitting in 'applied' for 14+ days with no status
    change -- not necessarily a problem, but worth surfacing rather than
    letting them silently age out of attention."""
    cutoff = (today or date.today()) - timedelta(days=_STALE_AFTER_DAYS)
    return await pool.fetch(
        """SELECT * FROM applications
           WHERE user_id = $1 AND status = 'applied' AND applied_at::date <= $2
           ORDER BY applied_at""",
        user_id,
        cutoff,
    )


async def funnel_stats(pool: asyncpg.Pool, user_id: int) -> dict:
    """Total applications and the conversion rate at each funnel stage --
    the only way to tell whether a low response rate is a volume problem
    or a fit problem."""
    counts = await pool.fetch(
        "SELECT status, count(*) AS n FROM applications WHERE user_id = $1 GROUP BY status",
        user_id,
    )
    by_status = {row["status"]: row["n"] for row in counts}
    total = sum(by_status.values())

    interviewing = by_status.get("interviewing", 0)
    offer = by_status.get("offer", 0)

    return {
        "total": total,
        "by_status": by_status,
        "interview_rate": round(100 * interviewing / total) if total else 0,
        "offer_rate": round(100 * offer / total) if total else 0,
    }
