from datetime import date, datetime, timezone

import pytest

from app.auth.service import create_user
from app.core.db import get_pool
from app.practice.fsrs_scheduler import retrievability, schedule_review
from app.practice.service import create_question


async def _make_question(pool, email: str) -> tuple[int, int]:
    user_id = await create_user(pool, email, "testpassword123", "Test")
    question_id = await create_question(pool, user_id, {"question": "Q", "answer": "A", "topic": "t"})
    return user_id, question_id


async def test_first_review_creates_a_card_state_row():
    pool = await get_pool()
    user_id, question_id = await _make_question(pool, "fsrs-first@example.com")

    review_date = await schedule_review(pool, user_id, question_id, rating=3)
    assert isinstance(review_date, date)
    assert review_date >= date.today()

    row = await pool.fetchrow("SELECT * FROM card_states WHERE question_id = $1", question_id)
    assert row is not None
    assert row["user_id"] == user_id
    assert row["stability"] is not None
    assert row["difficulty"] is not None
    assert row["due"].date() == review_date


async def test_easy_schedules_further_out_than_again_from_a_fresh_card():
    pool = await get_pool()
    user_id_a, q_easy = await _make_question(pool, "fsrs-easy@example.com")
    user_id_b, q_again = await _make_question(pool, "fsrs-again@example.com")

    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    easy_due = await schedule_review(pool, user_id_a, q_easy, rating=5, now=now)
    again_due = await schedule_review(pool, user_id_b, q_again, rating=1, now=now)

    assert easy_due > again_due


async def test_a_lapse_on_an_established_card_still_produces_a_future_due_date():
    pool = await get_pool()
    user_id, question_id = await _make_question(pool, "fsrs-lapse@example.com")

    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    await schedule_review(pool, user_id, question_id, rating=4, now=now)
    await schedule_review(pool, user_id, question_id, rating=5, now=now)
    lapse_due = await schedule_review(pool, user_id, question_id, rating=1, now=now)

    assert lapse_due >= now.date()


async def test_invalid_rating_is_rejected():
    pool = await get_pool()
    user_id, question_id = await _make_question(pool, "fsrs-badrating@example.com")

    with pytest.raises(ValueError):
        await schedule_review(pool, user_id, question_id, rating=0)
    with pytest.raises(ValueError):
        await schedule_review(pool, user_id, question_id, rating=6)


async def test_retrievability_is_none_before_first_review_and_a_probability_after():
    pool = await get_pool()
    user_id, question_id = await _make_question(pool, "fsrs-retrievability@example.com")

    assert await retrievability(pool, question_id) is None

    await schedule_review(pool, user_id, question_id, rating=3)
    r = await retrievability(pool, question_id)
    assert r is not None
    assert 0.0 <= r <= 1.0
