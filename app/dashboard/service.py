from __future__ import annotations

import math
from datetime import date, timedelta

import asyncpg

from app.core.usertime import canonical_zone_name
from app.practice import fsrs_scheduler

# Phase 5.4 mastery-score tuning. Kept as module constants (not magic
# numbers inline) so they're one place to retune and easy to reference
# from tests.
_RECENCY_HALF_LIFE_DAYS = 14  # a rating from 2 weeks ago counts half as much as one from today
_DIFFICULTY_MULTIPLIERS = {"easy": 0.85, "medium": 1.0, "hard": 1.2}
_RETRIEVABILITY_WEIGHT = 0.4  # how much FSRS's own recall estimate counts vs. self-rated confidence
_SHRINKAGE_K = 3  # attempt count at which the neutral prior's pull is halved
_NEUTRAL_PRIOR = 50.0  # score a topic with zero real signal is shrunk toward


async def get_stats(pool: asyncpg.Pool, user_id: int, today: date | None = None) -> dict:
    today = today or date.today()
    total_questions = await pool.fetchval("SELECT count(*) FROM questions WHERE user_id = $1", user_id)
    total_attempts = await pool.fetchval("SELECT count(*) FROM attempts WHERE user_id = $1", user_id)
    # `today` is the user's own local study day (see app.core.usertime), so
    # this count matches exactly what the review queue will hand them.
    due_today = await pool.fetchval(
        """SELECT count(*) FROM questions q
           LEFT JOIN card_states cs ON cs.question_id = q.question_id
           WHERE q.user_id = $1 AND (cs.due IS NULL OR cs.due::date <= $2)""",
        user_id,
        today,
    )
    # 30-day rolling window, not all-time -- an all-time average stops
    # reflecting anything after the first month of use.
    avg_confidence = await pool.fetchval(
        """SELECT round(avg(confidence_rating), 1) FROM attempts
           WHERE user_id = $1 AND practiced_at >= $2""",
        user_id,
        today - timedelta(days=30),
    )

    return {
        "total_questions": total_questions,
        "total_attempts": total_attempts,
        "due_today": due_today,
        "avg_confidence": avg_confidence,
    }


async def topic_mastery(pool: asyncpg.Pool, user_id: int) -> list[dict]:
    """Per-topic mastery score (0-100), weakest first -- turns raw attempt
    history into "what should I study" instead of four vanity counters.

    Phase 5.4: plain average confidence treats a 5/5 rating from three
    months ago the same as one from this morning, an "easy" question the
    same as a "hard" one, and says nothing about whether FSRS actually
    still expects you to recall it. This blends four signals instead:

    - recency-weighted confidence: exponential decay, 14-day half-life,
      so recent attempts dominate a stale first impression
    - difficulty-multiplied: a confident answer on a "hard" question says
      more about mastery than an equally confident one on an "easy" one,
      so the confidence component is scaled up or down (0.85x-1.2x) by
      the topic's own recency-weighted average difficulty
    - FSRS retrievability: the scheduler's own current recall-probability
      estimate per question (reusing card_states via fsrs_scheduler,
      already computed for the review queue), blended in at 40% so this
      isn't just a second, disconnected opinion
    - shrinkage toward a neutral 50 prior, fading out by ~3 attempts, so
      one lucky or unlucky attempt can't swing a topic to either end of
      the list
    """
    rows = await pool.fetch(
        """SELECT a.question_id, coalesce(nullif(q.topic, ''), 'untagged') AS topic,
                  q.difficulty, a.confidence_rating, a.practiced_at
           FROM attempts a
           JOIN questions q ON q.question_id = a.question_id
           WHERE q.user_id = $1""",
        user_id,
    )
    if not rows:
        return []

    retrievability = await fsrs_scheduler.retrievability_bulk(pool, user_id)
    today = date.today()

    topics: dict[str, dict] = {}
    for row in rows:
        bucket = topics.setdefault(
            row["topic"],
            {
                "weighted_confidence_sum": 0.0,
                "weighted_difficulty_sum": 0.0,
                "weight_sum": 0.0,
                "confidence_sum": 0,
                "attempt_count": 0,
                "retrievability_sum": 0.0,
                "retrievability_count": 0,
            },
        )
        days_ago = max((today - row["practiced_at"].date()).days, 0)
        # Recency alone drives the averaging weight. Difficulty is tracked
        # separately (as a recency-weighted average multiplier, applied
        # after) rather than folded into this same weight -- multiplying
        # every attempt in a topic by the same difficulty constant would
        # cancel out of a weighted average entirely when a topic's
        # questions share one difficulty, which is the common case.
        weight = math.exp(-days_ago / _RECENCY_HALF_LIFE_DAYS)

        bucket["weighted_confidence_sum"] += row["confidence_rating"] * weight
        bucket["weighted_difficulty_sum"] += _DIFFICULTY_MULTIPLIERS.get(row["difficulty"], 1.0) * weight
        bucket["weight_sum"] += weight
        bucket["confidence_sum"] += row["confidence_rating"]
        bucket["attempt_count"] += 1

        r = retrievability.get(row["question_id"])
        if r is not None:
            bucket["retrievability_sum"] += r
            bucket["retrievability_count"] += 1

    results = []
    for topic, b in topics.items():
        avg_confidence = round(b["confidence_sum"] / b["attempt_count"], 1)
        weighted_confidence = (
            b["weighted_confidence_sum"] / b["weight_sum"] if b["weight_sum"] else avg_confidence
        )
        difficulty_multiplier = b["weighted_difficulty_sum"] / b["weight_sum"] if b["weight_sum"] else 1.0
        confidence_component = min((weighted_confidence / 5) * 100 * difficulty_multiplier, 100.0)

        if b["retrievability_count"]:
            retrievability_component = (b["retrievability_sum"] / b["retrievability_count"]) * 100
            blended = (
                1 - _RETRIEVABILITY_WEIGHT
            ) * confidence_component + _RETRIEVABILITY_WEIGHT * retrievability_component
        else:
            blended = confidence_component

        shrinkage_weight = b["attempt_count"] / (b["attempt_count"] + _SHRINKAGE_K)
        mastery_score = shrinkage_weight * blended + (1 - shrinkage_weight) * _NEUTRAL_PRIOR

        results.append(
            {
                "topic": topic,
                "avg_confidence": avg_confidence,
                "attempt_count": b["attempt_count"],
                "mastery_score": round(mastery_score),
            }
        )

    results.sort(key=lambda r: (r["mastery_score"], -r["attempt_count"]))
    return results


async def activity_last_7_days(
    pool: asyncpg.Pool, user_id: int, today: date | None = None, tz_name: str = ""
) -> list[dict]:
    """One row per of the last 7 calendar days (oldest first, today last),
    zero-filled for days with no activity -- generate_series first, then
    LEFT JOIN the real counts onto it, so a quiet day shows as 0 instead
    of just not appearing in the heatmap at all.

    Days are the user's local days: the window anchors on their `today` and
    each attempt is bucketed by its timestamp shifted into their timezone,
    so the heatmap and the streak agree on where a day starts."""
    today = today or date.today()
    rows = await pool.fetch(
        """SELECT d::date AS day, count(a.attempt_id) AS attempt_count
           FROM generate_series($2::date - interval '6 days', $2::date, interval '1 day') AS d
           LEFT JOIN attempts a ON a.user_id = $1
                AND (a.practiced_at AT TIME ZONE $3)::date = d::date
           GROUP BY d
           ORDER BY d""",
        user_id,
        today,
        canonical_zone_name(tz_name),
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
