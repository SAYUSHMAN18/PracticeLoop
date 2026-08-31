import re

from httpx import ASGITransport, AsyncClient

from app.auth.service import create_user
from app.core.db import get_pool
from app.practice.service import build_daily_plan, create_question, record_attempt
from tests.conftest import signup


async def _create_question(client, question, topic="t", difficulty="medium"):
    response = await client.post(
        "/practice", data={"question": question, "answer": "A", "topic": topic, "difficulty": difficulty}
    )
    assert response.status_code == 303


async def _question_ids(client) -> list[str]:
    bank = await client.get("/practice")
    return re.findall(r"/practice/(\d+)/edit", bank.text)


# ---------- build_daily_plan categorization, tested directly against the
# service layer -- precise about *which* question gets *which* reason,
# which HTML-scraping the rendered page can't easily assert on. ----------


async def test_plan_labels_the_oldest_never_attempted_question_as_new():
    pool = await get_pool()
    user_id = await create_user(pool, "plan-svc-new@example.com", "testpassword123", "Test")
    older_id = await create_question(pool, user_id, {"question": "Older", "topic": "t"})
    await create_question(pool, user_id, {"question": "Newer", "topic": "t"})

    plan = await build_daily_plan(pool, user_id)
    reasons = {item["question"]["question_id"]: item["reason"] for item in plan}
    assert reasons[older_id] == "new"


async def test_plan_includes_a_challenge_pick_only_when_not_already_due():
    pool = await get_pool()
    user_id = await create_user(pool, "plan-svc-challenge@example.com", "testpassword123", "Test")
    hard_id = await create_question(
        pool, user_id, {"question": "Hard one", "topic": "hardtopic", "difficulty": "hard"}
    )

    # Untouched, so it's already in the "due" bucket -- no separate
    # "challenge" pick can exist yet (there's nothing else to pick).
    plan = await build_daily_plan(pool, user_id)
    assert {item["question"]["question_id"]: item["reason"] for item in plan} == {hard_id: "new"}

    # A separate, clearly-weaker topic, so the weak-topic bonus pick
    # doesn't end up competing with the hard question for the same slot.
    filler_id = await create_question(pool, user_id, {"question": "Filler", "topic": "filler"})
    await record_attempt(pool, user_id, filler_id, rating=1, feedback="")

    # Rated "easy" (5), FSRS pushes it days out -- now it's legitimately
    # not due, and becomes available as a distinct challenge pick.
    await record_attempt(pool, user_id, hard_id, rating=5, feedback="")
    plan_after = await build_daily_plan(pool, user_id)
    reasons_after = {item["question"]["question_id"]: item["reason"] for item in plan_after}
    assert reasons_after.get(hard_id) == "challenge"


async def test_plan_includes_a_weak_topic_pick_only_when_not_already_due():
    pool = await get_pool()
    user_id = await create_user(pool, "plan-svc-weak@example.com", "testpassword123", "Test")
    weak_id = await create_question(pool, user_id, {"question": "Weak topic Q1", "topic": "weak"})
    weak_id_2 = await create_question(pool, user_id, {"question": "Weak topic Q2", "topic": "weak"})
    other_id = await create_question(pool, user_id, {"question": "Other topic Q", "topic": "other"})

    # Push every question past "due today" first -- an untouched question
    # is always immediately due, so there'd be nothing to distinguish a
    # genuine "bonus" pick from plain "due" otherwise. Both weak-topic
    # questions rated low, the other topic rated high, so "weak" is
    # unambiguously the weaker of the two topics.
    await record_attempt(pool, user_id, weak_id, rating=1, feedback="")
    await record_attempt(pool, user_id, weak_id_2, rating=1, feedback="")
    await record_attempt(pool, user_id, other_id, rating=5, feedback="")

    plan = await build_daily_plan(pool, user_id)
    reasons = {item["question"]["question_id"]: item["reason"] for item in plan}
    # Oldest of the two weak-topic questions, both otherwise no longer due.
    assert reasons.get(weak_id) == "weak"


# ---------- router-level: session/flow mechanics ----------


async def test_starting_the_plan_drives_the_review_queue(client):
    await signup(client, "plan-start@example.com")
    await _create_question(client, "Plan Q1")
    await _create_question(client, "Plan Q2")

    start = await client.post("/practice/plan/start")
    assert start.status_code == 303
    assert start.headers["location"] == "/practice/review"

    queue = await client.get("/practice/review")
    assert "2 remaining" in queue.text


async def test_rating_a_plan_card_removes_it_from_the_session_plan(client):
    await signup(client, "plan-rate@example.com")
    await _create_question(client, "Plan Rate Q1")
    await _create_question(client, "Plan Rate Q2")
    await client.post("/practice/plan/start")

    ids = await _question_ids(client)
    rated = await client.post(f"/practice/review/{ids[0]}", data={"rating": 3})
    assert rated.status_code == 200

    next_card = await client.get("/practice/review/next")
    assert "1 remaining" in next_card.text


async def test_finished_plan_shows_plan_specific_completion_message(client):
    await signup(client, "plan-finish@example.com")
    await _create_question(client, "Only Plan Question")
    await client.post("/practice/plan/start")

    ids = await _question_ids(client)
    await client.post(f"/practice/review/{ids[0]}", data={"rating": 3})

    finished = await client.get("/practice/review")
    assert "Today's plan is complete" in finished.text
    assert "All caught up" not in finished.text


async def test_skip_removes_a_question_from_the_plan_without_recording_an_attempt(client):
    await signup(client, "plan-skip@example.com")
    await _create_question(client, "Skip Me")
    await _create_question(client, "Keep Me")
    await client.post("/practice/plan/start")

    queue = await client.get("/practice/review")
    shown_id = re.search(r'hx-post="/practice/review/(\d+)/skip"', queue.text).group(1)

    skipped = await client.post(f"/practice/review/{shown_id}/skip")
    assert skipped.status_code == 200
    assert "1 remaining" in skipped.text

    pool = await get_pool()
    attempts = await pool.fetch("SELECT * FROM attempts WHERE question_id = $1", int(shown_id))
    assert attempts == [], "skipping must not record an attempt"


async def test_plan_only_appears_in_normal_review_when_not_started(client):
    await signup(client, "plan-not-started@example.com")
    await _create_question(client, "Untouched Q")

    queue = await client.get("/practice/review")
    assert 'hx-post="/practice/review/' in queue.text
    assert "/skip" not in queue.text


async def test_plan_is_isolated_per_user(client):
    from app.main import app

    transport = ASGITransport(app=app)
    other_client = AsyncClient(transport=transport, base_url="http://test")
    await signup(other_client, "plan-other@example.com")
    await _create_question(other_client, "Other user's question")
    await other_client.post("/practice/plan/start")

    await signup(client, "plan-self@example.com")
    await _create_question(client, "My question")

    queue = await client.get("/practice/review")
    assert "Other user's question" not in queue.text
    assert "My question" in queue.text

    await other_client.aclose()
