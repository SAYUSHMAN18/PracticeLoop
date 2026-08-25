from __future__ import annotations

from datetime import date

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
    avg_confidence = await pool.fetchval(
        "SELECT round(avg(confidence_rating), 1) FROM attempts WHERE user_id = $1", user_id
    )

    return {
        "total_questions": total_questions,
        "total_attempts": total_attempts,
        "due_today": due_today,
        "avg_confidence": avg_confidence,
    }
