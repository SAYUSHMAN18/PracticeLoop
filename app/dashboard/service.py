from __future__ import annotations

from datetime import date, timedelta

import asyncpg


async def get_stats(pool: asyncpg.Pool, user_id: int) -> dict:
    total_questions = await pool.fetchval("SELECT count(*) FROM questions WHERE user_id = $1", user_id)
    total_attempts = await pool.fetchval("SELECT count(*) FROM attempts WHERE user_id = $1", user_id)
    due_today = await pool.fetchval(
        """SELECT count(*) FROM questions q
           LEFT JOIN card_states cs ON cs.question_id = q.question_id
           WHERE q.user_id = $1 AND (cs.due IS NULL OR cs.due::date <= $2)""",
        user_id,
        date.today(),
    )
    # 30-day rolling window, not all-time -- an all-time average stops
    # reflecting anything after the first month of use.
    avg_confidence = await pool.fetchval(
        """SELECT round(avg(confidence_rating), 1) FROM attempts
           WHERE user_id = $1 AND practiced_at >= $2""",
        user_id,
        date.today() - timedelta(days=30),
    )

    return {
        "total_questions": total_questions,
        "total_attempts": total_attempts,
        "due_today": due_today,
        "avg_confidence": avg_confidence,
    }


async def topic_mastery(pool: asyncpg.Pool, user_id: int) -> list[asyncpg.Record]:
    """Average confidence per topic, weakest first -- turns raw attempt
    history into "what should I study" instead of four vanity counters."""
    return await pool.fetch(
        """SELECT
               coalesce(nullif(q.topic, ''), 'untagged') AS topic,
               round(avg(a.confidence_rating), 1) AS avg_confidence,
               count(a.attempt_id) AS attempt_count
           FROM questions q
           JOIN attempts a ON a.question_id = q.question_id
           WHERE q.user_id = $1
           GROUP BY topic
           ORDER BY avg_confidence ASC, attempt_count DESC""",
        user_id,
    )


async def activity_last_7_days(pool: asyncpg.Pool, user_id: int) -> list[dict]:
    """One row per of the last 7 calendar days (oldest first, today last),
    zero-filled for days with no activity -- generate_series first, then
    LEFT JOIN the real counts onto it, so a quiet day shows as 0 instead
    of just not appearing in the heatmap at all."""
    rows = await pool.fetch(
        """SELECT d::date AS day, count(a.attempt_id) AS attempt_count
           FROM generate_series($2::date - interval '6 days', $2::date, interval '1 day') AS d
           LEFT JOIN attempts a ON a.user_id = $1 AND a.practiced_at::date = d::date
           GROUP BY d
           ORDER BY d""",
        user_id,
        date.today(),
    )
    return [{"day": r["day"], "attempt_count": r["attempt_count"]} for r in rows]


async def new_concept_recommendation(pool: asyncpg.Pool, user_id: int) -> asyncpg.Record | None:
    """One never-attempted question, oldest first -- deterministic (no
    randomness to make debugging or testing harder) and roughly matches
    "the thing you added and haven't gotten to yet" as a reasonable
    stand-in for a real concept graph's "next new concept" (Phase 5.1),
    which this app doesn't have."""
    return await pool.fetchrow(
        """SELECT q.question_id, q.question, q.topic FROM questions q
           WHERE q.user_id = $1
             AND NOT EXISTS (SELECT 1 FROM attempts a WHERE a.question_id = q.question_id)
           ORDER BY q.created_at ASC
           LIMIT 1""",
        user_id,
    )
