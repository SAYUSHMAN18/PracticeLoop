from __future__ import annotations

from datetime import date, timedelta

import asyncpg


async def get_stats(pool: asyncpg.Pool, user_id: int) -> dict:
    total_questions = await pool.fetchval(
        "SELECT count(*) FROM questions WHERE user_id = $1", user_id
    )
    total_attempts = await pool.fetchval(
        "SELECT count(*) FROM attempts WHERE user_id = $1", user_id
    )
    due_today = await pool.fetchval(
        """SELECT count(*) FROM questions q
           LEFT JOIN LATERAL (
               SELECT next_review_at FROM attempts a
               WHERE a.question_id = q.question_id
               ORDER BY practiced_at DESC LIMIT 1
           ) latest ON true
           WHERE q.user_id = $1 AND (latest.next_review_at IS NULL OR latest.next_review_at <= $2)""",
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
