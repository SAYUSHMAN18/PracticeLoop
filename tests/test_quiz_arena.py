from app.core.db import get_pool
from app.practice.service import create_question
from tests.conftest import signup


async def test_start_page_renders_with_no_questions_yet(client):
    await signup(client, "arena-empty@example.com")
    response = await client.get("/practice/quiz-arena")
    assert response.status_code == 200
    assert "All topics" in response.text


async def test_starting_with_no_matching_questions_shows_an_error(client):
    await signup(client, "arena-notopic@example.com")
    response = await client.post(
        "/practice/quiz-arena/start", data={"topic": "nonexistent-topic", "count": "10"}
    )
    assert response.status_code == 400
    assert "No questions match" in response.text


async def test_playing_without_starting_redirects_to_the_start_page(client):
    await signup(client, "arena-nostart@example.com")
    response = await client.get("/practice/quiz-arena/play", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/practice/quiz-arena"


async def test_full_quiz_arena_flow_mixed_free_text_and_mcq(client):
    await signup(client, "arena-flow@example.com")
    pool = await get_pool()
    user_id = await pool.fetchval("SELECT user_id FROM users WHERE email = $1", "arena-flow@example.com")

    await create_question(pool, user_id, {"question": "Free Q", "answer": "A", "topic": "mix"})
    await create_question(
        pool,
        user_id,
        {
            "question": "MCQ Q",
            "topic": "mix",
            "question_type": "multiple_choice",
            "choices": ["wrong", "right"],
            "correct_choice_index": 1,
        },
    )

    start = await client.post(
        "/practice/quiz-arena/start", data={"topic": "mix", "count": "5"}, follow_redirects=False
    )
    assert start.status_code == 303
    assert start.headers["location"] == "/practice/quiz-arena/play"

    # Answer both questions, whichever order the random pool gave them.
    for _ in range(2):
        play = await client.get("/practice/quiz-arena/play")
        assert play.status_code == 200
        if 'name="selected_index"' in play.text:
            await client.post("/practice/quiz-arena/answer", data={"selected_index": "1"})  # correct
        else:
            await client.post("/practice/quiz-arena/answer", data={"rating": "5"})  # strong recall

    result = await client.get("/practice/quiz-arena/result")
    assert result.status_code == 200
    assert "2/2" in result.text
    assert "100%" in result.text

    # The session state is cleared after the result page renders once.
    second_visit = await client.get("/practice/quiz-arena/result", follow_redirects=False)
    assert second_visit.status_code == 303
    assert second_visit.headers["location"] == "/practice/quiz-arena"


async def test_a_question_deleted_mid_quiz_is_skipped_not_a_500(client):
    await signup(client, "arena-deleted@example.com")
    pool = await get_pool()
    user_id = await pool.fetchval("SELECT user_id FROM users WHERE email = $1", "arena-deleted@example.com")
    q1 = await create_question(pool, user_id, {"question": "Q1", "answer": "A", "topic": "gone"})
    await create_question(pool, user_id, {"question": "Q2", "answer": "A", "topic": "gone"})

    await client.post("/practice/quiz-arena/start", data={"topic": "gone", "count": "5"})
    await pool.execute("DELETE FROM questions WHERE question_id = $1", q1)

    # Follows redirects explicitly: if the deleted question happened to be
    # first in the (randomly ordered) quiz, /play redirects to itself once
    # to skip past it before landing on a real question.
    play = await client.get("/practice/quiz-arena/play", follow_redirects=True)
    assert play.status_code == 200  # skipped the deleted one, landed on the survivor
    assert "Q2" in play.text
