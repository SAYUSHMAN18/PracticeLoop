"""'Today' is the user's today.

Every due date, the "due today" count, the daily-plan rollover and the
streak used to run off the server's UTC date. For a learner east or west
of UTC that means the day flips at the wrong local hour: a streak breaks
early, two consecutive local study days can collapse into one UTC day (or
one local day split across two), cards come due overnight. These tests
pin the fix -- the timezone (and optional Anki-style rollover hour) on
the profile now drives all of it.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.auth.service import create_user
from app.core.db import get_pool
from app.core.usertime import canonical_zone_name, resolve_zone, today_for
from app.practice.service import (
    create_question,
    due_for_review,
    record_attempt,
    streak_days,
    user_today,
)

# UTC+14. Local noon is 22:00 the previous UTC day, so which UTC calendar
# day an attempt lands on and which local day it lands on routinely differ
# -- the cleanest place to catch a UTC-based miscount.
FAR_EAST = "Pacific/Kiritimati"


# ---------- pure helpers ----------


def test_resolve_zone_falls_back_to_utc_for_junk():
    assert resolve_zone("") is timezone.utc
    assert resolve_zone(None) is timezone.utc
    assert resolve_zone("Not/A/Zone") is timezone.utc
    assert resolve_zone("Asia/Kolkata").key == "Asia/Kolkata"


def test_canonical_zone_name_is_sql_safe():
    # Postgres AT TIME ZONE raises on an unknown name -- anything that
    # doesn't resolve has to come back as a real one.
    assert canonical_zone_name("garbage") == "UTC"
    assert canonical_zone_name("") == "UTC"
    assert canonical_zone_name("America/New_York") == "America/New_York"


def test_today_for_respects_the_rollover_hour():
    # With a 4-hour rollover, the hours 00:00-03:59 still count as
    # yesterday; from 04:00 on it matches a plain midnight boundary.
    plain = today_for("UTC", 0)
    shifted = today_for("UTC", 4)
    if datetime.now(timezone.utc).hour < 4:
        assert plain - shifted == timedelta(days=1)
    else:
        assert plain == shifted


# ---------- streak_days, timezone-aware ----------


async def _set_profile(pool, user_id, **cols):
    sets = ", ".join(f"{k} = ${i + 2}" for i, k in enumerate(cols))
    await pool.execute(f"UPDATE profiles SET {sets} WHERE user_id = $1", user_id, *cols.values())


async def _attempt_on_local_day(pool, user_id, tz_name, days_ago, hour=12):
    """A recorded attempt whose practiced_at is `hour:00` local time on
    (the user's local today - days_ago), stored as the matching UTC
    instant."""
    qid = await create_question(pool, user_id, {"question": f"q{days_ago}-{hour}", "topic": "t"})
    await record_attempt(pool, user_id, qid, rating=3)
    local_day = today_for(tz_name) - timedelta(days=days_ago)
    local_dt = datetime(local_day.year, local_day.month, local_day.day, hour, 0, tzinfo=resolve_zone(tz_name))
    await pool.execute(
        "UPDATE attempts SET practiced_at = $2 WHERE question_id = $1",
        qid,
        local_dt.astimezone(timezone.utc),
    )


async def test_streak_counts_three_consecutive_local_days():
    pool = await get_pool()
    user_id = await create_user(pool, "tz-streak@example.com", "testpassword123", "TZ")
    await _set_profile(pool, user_id, timezone=FAR_EAST)

    for days_ago in (2, 1, 0):
        await _attempt_on_local_day(pool, user_id, FAR_EAST, days_ago)

    assert await streak_days(pool, user_id) == 3


async def test_two_local_days_that_share_a_utc_date_still_count_as_two():
    # The split case: 23:00 local one day and 01:00 local the next, in
    # UTC+14, both fall on the *same* UTC calendar day. The old
    # practiced_at::date logic saw one day and reported a streak of 1.
    pool = await get_pool()
    user_id = await create_user(pool, "tz-split@example.com", "testpassword123", "TZ")
    await _set_profile(pool, user_id, timezone=FAR_EAST)

    await _attempt_on_local_day(pool, user_id, FAR_EAST, days_ago=1, hour=23)
    await _attempt_on_local_day(pool, user_id, FAR_EAST, days_ago=0, hour=1)

    assert await streak_days(pool, user_id) == 2


async def test_streak_breaks_on_a_genuinely_missed_local_day():
    pool = await get_pool()
    user_id = await create_user(pool, "tz-break@example.com", "testpassword123", "TZ")
    await _set_profile(pool, user_id, timezone=FAR_EAST)

    await _attempt_on_local_day(pool, user_id, FAR_EAST, days_ago=3)
    await _attempt_on_local_day(pool, user_id, FAR_EAST, days_ago=0)

    assert await streak_days(pool, user_id) == 1  # today only


async def test_rollover_hour_shifts_the_users_day_boundary():
    pool = await get_pool()
    user_id = await create_user(pool, "tz-rollover@example.com", "testpassword123", "TZ")

    # Three sessions at 02:00 UTC on three consecutive calendar dates
    # ending with the current one. With a 4am rollover every one of them
    # counts for the day *before* its clock date, so the run of rolled
    # dates is still three consecutive days and the streak holds at 3 --
    # where a naive ::date would put the 02:00-today session on today and
    # the others a day earlier each, same length but a different anchor.
    await _set_profile(pool, user_id, timezone="UTC", day_rollover_hour=4)
    for days_ago in (0, 1, 2):
        qid = await create_question(pool, user_id, {"question": f"q{days_ago}", "topic": "t"})
        await record_attempt(pool, user_id, qid, rating=3)
        day = datetime.now(timezone.utc).date() - timedelta(days=days_ago)
        await pool.execute(
            "UPDATE attempts SET practiced_at = $2 WHERE question_id = $1",
            qid,
            datetime(day.year, day.month, day.day, 2, 0, tzinfo=timezone.utc),
        )

    assert await streak_days(pool, user_id) == 3
    # user_today with a 4am rollover is the clock date only once it's past
    # 04:00 UTC; before that it's still yesterday.
    expected = today_for("UTC", 4)
    assert await user_today(pool, user_id) == expected


async def test_a_streak_shield_bridges_one_missed_day():
    pool = await get_pool()
    user_id = await create_user(pool, "tz-shield@example.com", "testpassword123", "TZ")
    await _set_profile(pool, user_id, timezone="UTC", streak_shields=1)

    await _attempt_on_local_day(pool, user_id, "UTC", days_ago=2)
    await _attempt_on_local_day(pool, user_id, "UTC", days_ago=0)  # gap at -1

    assert await streak_days(pool, user_id) == 3


async def test_a_shield_does_not_bridge_a_two_day_hole():
    pool = await get_pool()
    user_id = await create_user(pool, "tz-shield2@example.com", "testpassword123", "TZ")
    await _set_profile(pool, user_id, timezone="UTC", streak_shields=3)

    await _attempt_on_local_day(pool, user_id, "UTC", days_ago=3)
    await _attempt_on_local_day(pool, user_id, "UTC", days_ago=0)  # -1 and -2 missing

    assert await streak_days(pool, user_id) == 1


# ---------- user_today / due_for_review ----------


async def test_user_today_matches_the_profile_timezone():
    pool = await get_pool()
    user_id = await create_user(pool, "tz-today@example.com", "testpassword123", "TZ")
    await _set_profile(pool, user_id, timezone=FAR_EAST)

    assert await user_today(pool, user_id) == today_for(FAR_EAST)


async def test_due_for_review_honours_an_explicit_today():
    pool = await get_pool()
    user_id = await create_user(pool, "tz-due@example.com", "testpassword123", "TZ")
    qid = await create_question(pool, user_id, {"question": "Q", "topic": "t"})
    await record_attempt(pool, user_id, qid, rating=5)  # pushes `due` into the future

    # As of a date before the next review, nothing is due; well after it,
    # the card is back.
    soon = await due_for_review(pool, user_id, today=datetime.now(timezone.utc).date())
    later = await due_for_review(
        pool, user_id, today=datetime.now(timezone.utc).date() + timedelta(days=3650)
    )
    assert qid not in [r["question_id"] for r in soon]
    assert qid in [r["question_id"] for r in later]
