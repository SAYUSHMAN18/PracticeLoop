from __future__ import annotations

import re

from app.auth.service import get_user_by_email
from app.core.db import get_pool
from app.practice import router as practice_router
from tests.conftest import signup


async def test_review_queue_shows_typed_answer_form_when_llm_configured(client):
    """Local test settings load the real .env, which has a real GROQ key --
    this confirms the default (per the plan) is graded review, not
    self-rating, whenever an LLM is actually configured."""
    await signup(client, "grading-default@example.com")
    await client.post("/practice", data={"question": "What is a mutex?", "answer": "A lock.", "topic": "t"})

    response = await client.get("/practice/review")
    assert 'name="answer"' in response.text
    assert 'name="rating"' not in response.text


async def test_review_queue_falls_back_to_self_rate_without_an_llm_key(client, monkeypatch):
    monkeypatch.setattr(practice_router, "llm_is_configured", lambda: False)

    await signup(client, "grading-fallback@example.com")
    await client.post("/practice", data={"question": "What is a mutex?", "answer": "A lock.", "topic": "t"})

    response = await client.get("/practice/review")
    assert 'name="rating"' in response.text
    assert 'name="answer"' not in response.text


async def test_review_queue_falls_back_to_self_rate_for_a_question_with_no_answer(client):
    """can_grade needs something to grade against -- an answerless
    question falls back even with an LLM configured."""
    await signup(client, "grading-noanswer@example.com")
    await client.post("/practice", data={"question": "What is a mutex?", "answer": "", "topic": "t"})

    response = await client.get("/practice/review")
    assert 'name="rating"' in response.text


async def test_grade_endpoint_records_the_graded_rating_not_a_self_rating(client, monkeypatch):
    async def fake_grade_answer(question, expected_answer, student_answer):
        return {"rating": 2, "feedback": "You missed the mutual-exclusion part."}

    monkeypatch.setattr(practice_router.grading, "grade_answer", fake_grade_answer)

    await signup(client, "grade-endpoint@example.com")
    await client.post("/practice", data={"question": "What is a mutex?", "answer": "A lock.", "topic": "t"})

    bank = await client.get("/practice")
    question_id = re.search(r"/practice/(\d+)/edit", bank.text).group(1)

    response = await client.post(
        f"/practice/review/{question_id}/grade", data={"answer": "Something about threads"}
    )
    assert response.status_code == 200
    assert "Graded 2/5" in response.text
    assert "mutual-exclusion" in response.text
    assert "A lock." in response.text  # the correct answer is shown

    pool = await get_pool()
    attempt = await pool.fetchrow(
        "SELECT confidence_rating FROM attempts WHERE question_id = $1", int(question_id)
    )
    assert attempt["confidence_rating"] == 2


async def test_grade_endpoint_falls_back_to_self_rate_on_grading_failure(client, monkeypatch):
    async def failing_grade_answer(question, expected_answer, student_answer):
        raise RuntimeError("LLM provider unavailable")

    monkeypatch.setattr(practice_router.grading, "grade_answer", failing_grade_answer)

    await signup(client, "grade-fail@example.com")
    await client.post("/practice", data={"question": "What is a mutex?", "answer": "A lock.", "topic": "t"})
    bank = await client.get("/practice")
    question_id = re.search(r"/practice/(\d+)/edit", bank.text).group(1)

    response = await client.post(f"/practice/review/{question_id}/grade", data={"answer": "anything"})
    assert response.status_code == 200
    assert 'name="rating"' in response.text  # fell back to the self-rate card

    pool = await get_pool()
    attempts = await pool.fetch("SELECT * FROM attempts WHERE question_id = $1", int(question_id))
    assert attempts == []  # a failed grade must not record a bogus attempt


async def test_grading_another_users_question_is_404(client):
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    victim = client
    attacker = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    await signup(victim, "grade-victim@example.com")
    await signup(attacker, "grade-attacker@example.com")

    await victim.post("/practice", data={"question": "Victim's question", "answer": "secret", "topic": ""})
    pool = await get_pool()
    victim_user = await get_user_by_email(pool, "grade-victim@example.com")
    question_id = await pool.fetchval(
        "SELECT question_id FROM questions WHERE user_id = $1", victim_user["user_id"]
    )

    response = await attacker.post(f"/practice/review/{question_id}/grade", data={"answer": "hacked"})
    assert response.status_code == 404

    await attacker.aclose()
