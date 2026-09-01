from app.analytics import service
from app.core.db import get_pool
from app.practice.service import create_question, record_attempt
from tests.conftest import signup


def test_get_recommendation_with_no_mastery_returns_none():
    assert service.get_recommendation([]) is None


def test_get_recommendation_explains_the_weakest_topic():
    mastery = [
        {"topic": "algebra", "mastery_score": 42, "attempt_count": 3, "avg_confidence": 2.0},
        {"topic": "geometry", "mastery_score": 80, "attempt_count": 5, "avg_confidence": 4.5},
    ]
    result = service.get_recommendation(mastery)
    assert result["topic"] == "algebra"
    assert "42/100" in result["explanation"]
    assert "3 attempt" in result["explanation"]


async def test_retention_is_empty_with_no_reviewed_questions():
    pool = await get_pool()
    from app.auth.service import create_user

    user_id = await create_user(pool, "analytics-noretention@example.com", "testpassword123", "Test")
    assert await service.get_retention_by_topic(pool, user_id) == []


async def test_retention_groups_by_topic_after_a_real_review():
    pool = await get_pool()
    from app.auth.service import create_user

    user_id = await create_user(pool, "analytics-retention@example.com", "testpassword123", "Test")
    q = await create_question(pool, user_id, {"question": "Q", "answer": "A", "topic": "js"})
    await record_attempt(pool, user_id, q, rating=5)

    retention = await service.get_retention_by_topic(pool, user_id)
    assert len(retention) == 1
    assert retention[0]["topic"] == "js"
    assert 0 <= retention[0]["retention_percent"] <= 100
    assert retention[0]["question_count"] == 1


async def test_timeline_merges_attempts_and_lessons_sorted_newest_first():
    pool = await get_pool()
    from app.auth.service import create_user
    from app.learning_paths.service import create_path, toggle_lesson

    user_id = await create_user(pool, "analytics-timeline@example.com", "testpassword123", "Test")
    q = await create_question(
        pool, user_id, {"question": "What is a hash map?", "answer": "A", "topic": "ds"}
    )
    await record_attempt(pool, user_id, q, rating=4)

    path_id = await create_path(pool, user_id, "Learn origami", ai_available=False)
    lesson_id = await pool.fetchval(
        """SELECT l.lesson_id FROM learning_lessons l
           JOIN learning_units u ON u.unit_id = l.unit_id
           JOIN learning_modules m ON m.module_id = u.module_id
           WHERE m.path_id = $1 ORDER BY l.position LIMIT 1""",
        path_id,
    )
    await toggle_lesson(pool, user_id, path_id, lesson_id)

    timeline = await service.get_timeline(pool, user_id)
    kinds = {event["kind"] for event in timeline}
    assert "attempt" in kinds
    assert "lesson" in kinds
    # newest-first: every entry's timestamp is >= the next one's
    for earlier, later in zip(timeline, timeline[1:], strict=False):
        assert earlier["at"] >= later["at"]


async def test_progress_page_renders_with_real_data(client):
    await signup(client, "analytics-page@example.com")
    pool = await get_pool()
    user_id = await pool.fetchval("SELECT user_id FROM users WHERE email = $1", "analytics-page@example.com")
    q = await create_question(pool, user_id, {"question": "Q", "answer": "A", "topic": "recursion"})
    await record_attempt(pool, user_id, q, rating=2)

    response = await client.get("/progress")
    assert response.status_code == 200
    assert "recursion" in response.text
    assert "What to study next" in response.text
    assert "recommended because" in response.text


async def test_progress_page_renders_with_no_activity_at_all(client):
    await signup(client, "analytics-empty@example.com")
    response = await client.get("/progress")
    assert response.status_code == 200
    assert "No practice activity yet" in response.text
    assert "No reviewed questions yet" in response.text
