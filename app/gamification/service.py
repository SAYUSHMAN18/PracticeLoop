from __future__ import annotations

import asyncio

import asyncpg

# Level N costs 50*N XP to clear (so level 1->2 needs 50, 2->3 needs 100,
# 3->4 needs 150, ...) -- cheap early levels for a fast first "level up"
# moment, increasingly costly later. Computed on the fly from a running
# XP total rather than stored, so it's never out of sync with the ledger.
_BASE_LEVEL_COST = 50


def _level_from_xp(total_xp: int) -> dict:
    level = 1
    xp_for_this_level = _BASE_LEVEL_COST
    xp_into_level = total_xp
    while xp_into_level >= xp_for_this_level:
        xp_into_level -= xp_for_this_level
        level += 1
        xp_for_this_level = _BASE_LEVEL_COST * level
    return {
        "level": level,
        "xp_into_level": xp_into_level,
        "xp_for_next_level": xp_for_this_level,
        "level_progress_percent": round(100 * xp_into_level / xp_for_this_level),
    }


async def award_xp(pool: asyncpg.Pool, user_id: int, source_type: str, source_id: int, amount: int) -> bool:
    """Idempotent: a duplicate (user_id, source_type, source_id) is
    silently skipped rather than erroring, since every call site is
    "award XP for this event if it hasn't already been awarded," not
    "this must be a new event." Returns whether XP was actually granted."""
    result = await pool.execute(
        """INSERT INTO xp_events (user_id, source_type, source_id, amount)
           VALUES ($1, $2, $3, $4)
           ON CONFLICT (user_id, source_type, source_id) DO NOTHING""",
        user_id,
        source_type,
        source_id,
        amount,
    )
    return result == "INSERT 0 1"


async def get_xp_summary(pool: asyncpg.Pool, user_id: int) -> dict:
    total_xp = await pool.fetchval(
        "SELECT coalesce(sum(amount), 0) FROM xp_events WHERE user_id = $1", user_id
    )
    return {"total_xp": total_xp, **_level_from_xp(total_xp)}


# (id, label, description, stats-key threshold) -- a fixed, small set
# computed live from real counts rather than a stored UserAchievement
# table. Simpler, always consistent with actual activity, and still
# gives every badge -- earned or not -- a real "how do I get this"
# description, matching the plan's own "milestones" more than a hidden
# surprise-unlock system would.
_BADGES = [
    {
        "id": "first_steps",
        "label": "First Steps",
        "description": "Answer your first practice question.",
        "stat": "attempts_total",
        "threshold": 1,
    },
    {
        "id": "lesson_learner",
        "label": "Lesson Learner",
        "description": "Complete your first lesson.",
        "stat": "lessons_completed",
        "threshold": 1,
    },
    {
        "id": "on_a_roll",
        "label": "On a Roll",
        "description": "Practice 7 days in a row.",
        "stat": "streak_days",
        "threshold": 7,
    },
    {
        "id": "half_century",
        "label": "Half Century",
        "description": "Answer 50 practice questions.",
        "stat": "attempts_total",
        "threshold": 50,
    },
    {
        "id": "path_finisher",
        "label": "Path Finisher",
        "description": "Complete an entire learning path.",
        "stat": "paths_completed",
        "threshold": 1,
    },
    {
        "id": "know_thyself",
        "label": "Know Thyself",
        "description": "Take your first diagnostic.",
        "stat": "diagnostics_taken",
        "threshold": 1,
    },
]


async def get_badges(pool: asyncpg.Pool, user_id: int, *, streak_days: int) -> list[dict]:
    """streak_days is passed in rather than recomputed here -- every page
    that would show badges (the dashboard) already computes it once for
    its own streak display; recomputing it a second time here would just
    be a duplicate query for a value the caller already has."""
    attempts_total, lessons_completed, paths_completed, diagnostics_taken = await _gather_stats(pool, user_id)
    stats = {
        "attempts_total": attempts_total,
        "lessons_completed": lessons_completed,
        "paths_completed": paths_completed,
        "diagnostics_taken": diagnostics_taken,
        "streak_days": streak_days,
    }
    return [{**badge, "earned": stats[badge["stat"]] >= badge["threshold"]} for badge in _BADGES]


async def _gather_stats(pool: asyncpg.Pool, user_id: int) -> tuple[int, int, int, int]:
    attempts_total, lessons_completed, paths_completed, diagnostics_taken = await asyncio.gather(
        pool.fetchval("SELECT count(*) FROM attempts WHERE user_id = $1", user_id),
        pool.fetchval(
            """SELECT count(*) FROM learning_lessons l
               JOIN learning_units u ON u.unit_id = l.unit_id
               JOIN learning_modules m ON m.module_id = u.module_id
               JOIN learning_paths p ON p.path_id = m.path_id
               WHERE p.user_id = $1 AND l.completed_at IS NOT NULL""",
            user_id,
        ),
        pool.fetchval(
            """SELECT count(*) FROM (
                   SELECT p.path_id
                   FROM learning_paths p
                   JOIN learning_modules m ON m.path_id = p.path_id
                   JOIN learning_units u ON u.module_id = m.module_id
                   JOIN learning_lessons l ON l.unit_id = u.unit_id
                   WHERE p.user_id = $1
                   GROUP BY p.path_id
                   HAVING count(*) = count(l.completed_at)
               ) AS fully_complete""",
            user_id,
        ),
        pool.fetchval("SELECT count(*) FROM diagnostic_attempts WHERE user_id = $1", user_id),
    )
    return attempts_total, lessons_completed, paths_completed, diagnostics_taken
