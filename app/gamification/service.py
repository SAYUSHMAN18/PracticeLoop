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


# (id, label, description, stats-key, threshold) -- a fixed set whose
# "earned" state is computed live from real counts rather than trusted
# from a stored flag, so it's always consistent with actual activity and
# every badge -- earned or not -- carries a real "how do I get this"
# description. The user_badges table (migration 0025) records only the
# first moment each one is earned, which is what lets a "you earned X"
# notification fire exactly once (see record_newly_earned).
#
# Tiers on the same stat (25 / 250 / 1000 attempts, 7 / 30 / 100 day
# streak) give a long-term user something still ahead of them without a
# per-badge bespoke condition.
_BADGES = [
    # -- practice volume --
    {
        "id": "first_steps",
        "label": "First Steps",
        "description": "Answer your first practice question.",
        "stat": "attempts_total",
        "threshold": 1,
    },
    {
        "id": "quarter_century",
        "label": "Quarter Century",
        "description": "Answer 25 practice questions.",
        "stat": "attempts_total",
        "threshold": 25,
    },
    {
        "id": "half_century",
        "label": "Half Century",
        "description": "Answer 50 practice questions.",
        "stat": "attempts_total",
        "threshold": 50,
    },
    {
        "id": "double_century",
        "label": "Double Century",
        "description": "Answer 250 practice questions.",
        "stat": "attempts_total",
        "threshold": 250,
    },
    {
        "id": "millennium",
        "label": "Millennium",
        "description": "Answer 1,000 practice questions.",
        "stat": "attempts_total",
        "threshold": 1000,
    },
    # -- streak --
    {
        "id": "on_a_roll",
        "label": "On a Roll",
        "description": "Practice 7 days in a row.",
        "stat": "streak_days",
        "threshold": 7,
    },
    {
        "id": "month_of_focus",
        "label": "Month of Focus",
        "description": "Practice 30 days in a row.",
        "stat": "streak_days",
        "threshold": 30,
    },
    {
        "id": "century_streak",
        "label": "Unbroken",
        "description": "Practice 100 days in a row.",
        "stat": "streak_days",
        "threshold": 100,
    },
    # -- lessons --
    {
        "id": "lesson_learner",
        "label": "Lesson Learner",
        "description": "Complete your first lesson.",
        "stat": "lessons_completed",
        "threshold": 1,
    },
    {
        "id": "ten_lessons",
        "label": "Coursework",
        "description": "Complete 10 lessons.",
        "stat": "lessons_completed",
        "threshold": 10,
    },
    {
        "id": "fifty_lessons",
        "label": "Scholar",
        "description": "Complete 50 lessons.",
        "stat": "lessons_completed",
        "threshold": 50,
    },
    # -- paths --
    {
        "id": "path_finisher",
        "label": "Path Finisher",
        "description": "Complete an entire learning path.",
        "stat": "paths_completed",
        "threshold": 1,
    },
    {
        "id": "three_paths",
        "label": "Polymath",
        "description": "Complete three learning paths.",
        "stat": "paths_completed",
        "threshold": 3,
    },
    # -- diagnostics --
    {
        "id": "know_thyself",
        "label": "Know Thyself",
        "description": "Take your first diagnostic.",
        "stat": "diagnostics_taken",
        "threshold": 1,
    },
    {
        "id": "five_diagnostics",
        "label": "Measured",
        "description": "Take five diagnostics.",
        "stat": "diagnostics_taken",
        "threshold": 5,
    },
    # -- bank building --
    {
        "id": "curator",
        "label": "Curator",
        "description": "Build a bank of 50 questions.",
        "stat": "questions_total",
        "threshold": 50,
    },
    # -- recall quality --
    {
        "id": "sharp_recall",
        "label": "Sharp Recall",
        "description": "Rate 25 reviews as an easy, confident recall.",
        "stat": "strong_recalls",
        "threshold": 25,
    },
]


async def get_badges(pool: asyncpg.Pool, user_id: int, *, streak_days: int) -> list[dict]:
    """streak_days is passed in rather than recomputed here -- every page
    that would show badges (the dashboard) already computes it once for
    its own streak display; recomputing it a second time here would just
    be a duplicate query for a value the caller already has.

    Pure: it never writes. record_newly_earned() does the "is this the
    first time?" side of things, from the list this returns."""
    counts = await _gather_stats(pool, user_id)
    stats = {**counts, "streak_days": streak_days}
    return [{**badge, "earned": stats[badge["stat"]] >= badge["threshold"]} for badge in _BADGES]


async def record_newly_earned(pool: asyncpg.Pool, user_id: int, badges: list[dict]) -> list[dict]:
    """Given the list get_badges() just produced, persist a first-earned
    row for any badge that's earned but not yet recorded, and drop one
    notification per genuinely new badge. Returns the newly-earned badges.

    Called from the dashboard route (the page that renders badges and
    that users hit constantly), not from get_badges itself -- keeping the
    read pure and the write in one obvious place."""
    earned_now = {b["id"] for b in badges if b["earned"]}
    if not earned_now:
        return []

    already = {
        r["badge_id"]
        for r in await pool.fetch(
            "SELECT badge_id FROM user_badges WHERE user_id = $1 AND badge_id = ANY($2::text[])",
            user_id,
            list(earned_now),
        )
    }
    fresh = [b for b in badges if b["id"] in earned_now and b["id"] not in already]
    if not fresh:
        return []

    from app.notifications.service import create as create_notification

    for badge in fresh:
        inserted = await pool.execute(
            """INSERT INTO user_badges (user_id, badge_id) VALUES ($1, $2)
               ON CONFLICT (user_id, badge_id) DO NOTHING""",
            user_id,
            badge["id"],
        )
        # A concurrent dashboard load could have won the race -- only
        # notify for the row this call actually inserted.
        if inserted == "INSERT 0 1":
            await create_notification(
                pool,
                user_id,
                "badge_earned",
                f"Badge earned: {badge['label']}",
                body=badge["description"],
                link="/dashboard",
            )
    return fresh


_STREAK_SHIELD_CAP = 3  # never hold more than this many at once
_STREAK_SHIELD_MIN_DAYS = 7  # a streak this long starts earning them


async def grant_streak_shield(pool: asyncpg.Pool, user_id: int, streak: int) -> bool:
    """One streak freeze per ISO week, while the streak is at least a week
    long and the user isn't already holding the cap. Idempotent within a
    week via profiles.streak_shield_week (the Monday of the week last
    credited). Returns whether one was actually granted.

    Called from the dashboard render, not the practice hot path -- a user
    who never looks at their dashboard also never sees a streak break, so
    there's nothing to protect there anyway."""
    if streak < _STREAK_SHIELD_MIN_DAYS:
        return False
    row = await pool.fetchrow(
        """UPDATE profiles
           SET streak_shields = streak_shields + 1, streak_shield_week = date_trunc('week', now())::date
           WHERE user_id = $1
             AND streak_shields < $2
             AND (streak_shield_week IS NULL OR streak_shield_week < date_trunc('week', now())::date)
           RETURNING streak_shields""",
        user_id,
        _STREAK_SHIELD_CAP,
    )
    return row is not None


async def _gather_stats(pool: asyncpg.Pool, user_id: int) -> dict[str, int]:
    (
        attempts_total,
        lessons_completed,
        paths_completed,
        diagnostics_taken,
        questions_total,
        strong_recalls,
    ) = await asyncio.gather(
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
        pool.fetchval("SELECT count(*) FROM questions WHERE user_id = $1", user_id),
        pool.fetchval("SELECT count(*) FROM attempts WHERE user_id = $1 AND confidence_rating >= 5", user_id),
    )
    return {
        "attempts_total": attempts_total,
        "lessons_completed": lessons_completed,
        "paths_completed": paths_completed,
        "diagnostics_taken": diagnostics_taken,
        "questions_total": questions_total,
        "strong_recalls": strong_recalls,
    }
