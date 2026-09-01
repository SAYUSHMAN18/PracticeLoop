from datetime import date

from app.auth.service import create_user
from app.core.db import get_pool
from app.practice.service import (
    QuestionNotFound,
    create_question,
    get_question,
    record_mcq_attempt,
)
from tests.conftest import signup


async def _make_mcq_question(pool, email: str) -> tuple[int, int]:
    user_id = await create_user(pool, email, "testpassword123", "Test")
    question_id = await create_question(
        pool,
        user_id,
        {
            "question": "Which data structure is LIFO?",
            "topic": "data structures",
            "question_type": "multiple_choice",
            "choices": ["Queue", "Stack", "Linked list", "Hash map"],
            "correct_choice_index": 1,
        },
    )
    return user_id, question_id


async def test_mcq_question_persists_choices_as_a_real_list_not_a_json_string():
    """Guards the jsonb type codec registered in app/core/db.py -- without
    it, `choices` would round-trip as a raw JSON string instead of a
    Python list, breaking every {% for choice in card.choices %} in the
    review template."""
    pool = await get_pool()
    user_id, question_id = await _make_mcq_question(pool, "mcq-jsonb@example.com")

    question = await get_question(pool, user_id, question_id)
    assert question["question_type"] == "multiple_choice"
    assert isinstance(question["choices"], list)
    assert question["choices"] == ["Queue", "Stack", "Linked list", "Hash map"]
    assert question["correct_choice_index"] == 1


async def test_correct_answer_is_graded_correct_and_schedules_a_future_review():
    pool = await get_pool()
    user_id, question_id = await _make_mcq_question(pool, "mcq-correct@example.com")

    review_date, is_correct = await record_mcq_attempt(pool, user_id, question_id, selected_index=1)
    assert is_correct is True
    assert review_date >= date.today()

    rating = await pool.fetchval("SELECT confidence_rating FROM attempts WHERE question_id = $1", question_id)
    assert rating == 4


async def test_wrong_answer_is_graded_incorrect():
    pool = await get_pool()
    user_id, question_id = await _make_mcq_question(pool, "mcq-wrong@example.com")

    review_date, is_correct = await record_mcq_attempt(pool, user_id, question_id, selected_index=0)
    assert is_correct is False

    rating = await pool.fetchval("SELECT confidence_rating FROM attempts WHERE question_id = $1", question_id)
    assert rating == 2


async def test_record_mcq_attempt_rejects_a_free_text_question():
    pool = await get_pool()
    user_id = await create_user(pool, "mcq-wrongtype@example.com", "testpassword123", "Test")
    question_id = await create_question(pool, user_id, {"question": "Free text Q", "answer": "A"})

    try:
        await record_mcq_attempt(pool, user_id, question_id, selected_index=0)
        raise AssertionError("expected QuestionNotFound")
    except QuestionNotFound:
        pass


async def test_review_queue_shows_choice_buttons_for_a_due_mcq_question(client):
    pool = await get_pool()
    user_id, question_id = await _make_mcq_question(pool, "mcq-review@example.com")
    # signup() creates its own separate user; attach this question's owner
    # by signing in as that same email/password combo instead.
    await client.post("/login", data={"email": "mcq-review@example.com", "password": "testpassword123"})

    response = await client.get("/practice/review")
    assert response.status_code == 200
    assert "Which data structure is LIFO?" in response.text
    assert 'name="selected_index" value="1"' in response.text
    assert "Stack" in response.text


async def test_answering_correctly_shows_correct_and_advances(client):
    await signup(client, "mcq-answer-correct@example.com")
    pool = await get_pool()
    row = await pool.fetchrow("SELECT user_id FROM users WHERE email = $1", "mcq-answer-correct@example.com")
    question_id = await create_question(
        pool,
        row["user_id"],
        {
            "question": "2 + 2?",
            "question_type": "multiple_choice",
            "choices": ["3", "4", "5"],
            "correct_choice_index": 1,
        },
    )

    response = await client.post(
        f"/practice/review/{question_id}/answer-choice", data={"selected_index": "1"}
    )
    assert response.status_code == 200
    assert "Correct!" in response.text


async def test_answering_incorrectly_shows_the_correct_choice(client):
    await signup(client, "mcq-answer-wrong@example.com")
    pool = await get_pool()
    row = await pool.fetchrow("SELECT user_id FROM users WHERE email = $1", "mcq-answer-wrong@example.com")
    question_id = await create_question(
        pool,
        row["user_id"],
        {
            "question": "2 + 2?",
            "question_type": "multiple_choice",
            "choices": ["3", "4", "5"],
            "correct_choice_index": 1,
        },
    )

    response = await client.post(
        f"/practice/review/{question_id}/answer-choice", data={"selected_index": "0"}
    )
    assert response.status_code == 200
    assert "Not quite." in response.text
    assert "Correct answer: 4" in response.text


async def test_another_users_mcq_question_404s_on_answer():
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    transport = ASGITransport(app=app)
    owner = AsyncClient(transport=transport, base_url="http://test")
    attacker = AsyncClient(transport=transport, base_url="http://test")

    pool = await get_pool()
    _, question_id = await _make_mcq_question(pool, "mcq-victim@example.com")
    await signup(attacker, "mcq-attacker@example.com")

    response = await attacker.post(
        f"/practice/review/{question_id}/answer-choice", data={"selected_index": "1"}
    )
    assert response.status_code == 404

    await owner.aclose()
    await attacker.aclose()
