"""The starter deck (data/starter_deck.json, ~79 questions) is seeded whole
so search and the question bank have real content on day one, but every
seeded question starts as a brand-new FSRS card -- due immediately -- so
unstaggered it used to hand a fresh signup a same-day plan of "78 due,
~158 minutes." seed_starter_deck spreads most of it into the future instead;
these lock in that only a small, sane first slice is due today.
"""

from app.auth.service import get_user_by_email
from app.core.db import get_pool
from app.practice.service import _SEED_IMMEDIATE, build_daily_plan, due_for_review


async def _signup_with_seed_deck(client, email: str) -> None:
    response = await client.post(
        "/signup",
        data={"name": "Seed Test", "email": email, "password": "testpassword123", "seed_deck": "true"},
    )
    assert response.status_code == 303, response.text


async def test_seeded_deck_is_not_all_due_on_day_one(client):
    await _signup_with_seed_deck(client, "seed-pacing@example.com")

    pool = await get_pool()
    user = await get_user_by_email(pool, "seed-pacing@example.com")
    due = await due_for_review(pool, user["user_id"])

    assert len(due) == _SEED_IMMEDIATE
    assert 0 < _SEED_IMMEDIATE < 79


async def test_todays_plan_is_a_short_session_not_the_whole_deck(client):
    await _signup_with_seed_deck(client, "seed-pacing-plan@example.com")

    pool = await get_pool()
    user = await get_user_by_email(pool, "seed-pacing-plan@example.com")
    plan = await build_daily_plan(pool, user["user_id"])

    # _SEED_IMMEDIATE due questions, plus up to one weak-topic and one
    # hard-difficulty bonus pick -- nowhere near the full ~79-question deck.
    assert len(plan) <= _SEED_IMMEDIATE + 2
