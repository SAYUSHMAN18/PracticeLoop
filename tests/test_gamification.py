from app.assessments import service as assessments_service
from app.auth.service import create_user
from app.core.db import get_pool
from app.gamification.service import award_xp, get_badges, get_xp_summary
from app.learning_paths.service import create_path
from app.practice.service import create_question, record_attempt, record_mcq_attempt
from tests.conftest import signup


async def test_xp_summary_level_boundaries():
    pool = await get_pool()
    user_id = await create_user(pool, "xp-levels@example.com", "testpassword123", "Test")

    summary = await get_xp_summary(pool, user_id)
    assert summary == {
        "total_xp": 0,
        "level": 1,
        "xp_into_level": 0,
        "xp_for_next_level": 50,
        "level_progress_percent": 0,
    }

    await award_xp(pool, user_id, "practice_attempt", 1, 50)  # exactly clears level 1
    summary = await get_xp_summary(pool, user_id)
    assert summary["level"] == 2
    assert summary["xp_into_level"] == 0
    assert summary["xp_for_next_level"] == 100  # level 2 costs 50*2

    await award_xp(pool, user_id, "practice_attempt", 2, 40)
    summary = await get_xp_summary(pool, user_id)
    assert summary["level"] == 2
    assert summary["xp_into_level"] == 40
    assert summary["level_progress_percent"] == 40


async def test_award_xp_is_idempotent_per_source():
    pool = await get_pool()
    user_id = await create_user(pool, "xp-idempotent@example.com", "testpassword123", "Test")

    granted_first = await award_xp(pool, user_id, "lesson_complete", 42, 15)
    granted_second = await award_xp(pool, user_id, "lesson_complete", 42, 15)
    assert granted_first is True
    assert granted_second is False

    summary = await get_xp_summary(pool, user_id)
    assert summary["total_xp"] == 15  # not 30


async def test_free_text_attempt_awards_xp_tiered_by_rating():
    pool = await get_pool()
    user_id = await create_user(pool, "xp-freetext@example.com", "testpassword123", "Test")
    q1 = await create_question(pool, user_id, {"question": "Q1", "answer": "A", "topic": "t"})
    q2 = await create_question(pool, user_id, {"question": "Q2", "answer": "A", "topic": "t"})
    q3 = await create_question(pool, user_id, {"question": "Q3", "answer": "A", "topic": "t"})

    await record_attempt(pool, user_id, q1, rating=5)  # strong -> 10
    await record_attempt(pool, user_id, q2, rating=3)  # okay -> 6
    await record_attempt(pool, user_id, q3, rating=1)  # weak -> 3

    summary = await get_xp_summary(pool, user_id)
    assert summary["total_xp"] == 19


async def test_mcq_attempt_awards_xp_through_the_shared_record_attempt_path():
    pool = await get_pool()
    user_id = await create_user(pool, "xp-mcq@example.com", "testpassword123", "Test")
    correct_q = await create_question(
        pool,
        user_id,
        {
            "question": "2+2?",
            "question_type": "multiple_choice",
            "choices": ["3", "4"],
            "correct_choice_index": 1,
        },
    )
    wrong_q = await create_question(
        pool,
        user_id,
        {
            "question": "3+3?",
            "question_type": "multiple_choice",
            "choices": ["6", "7"],
            "correct_choice_index": 0,
        },
    )

    await record_mcq_attempt(pool, user_id, correct_q, selected_index=1)  # correct -> rating 4 -> 10 XP
    await record_mcq_attempt(pool, user_id, wrong_q, selected_index=1)  # wrong -> rating 2 -> 3 XP

    summary = await get_xp_summary(pool, user_id)
    assert summary["total_xp"] == 13


async def test_completing_a_lesson_awards_xp_once_even_if_toggled_repeatedly(client):
    await signup(client, "xp-lesson@example.com")
    create = await client.post("/learning-paths", data={"goal": "Learn origami"}, follow_redirects=False)
    import re

    path_id = re.search(r"/learning-paths/(\d+)", create.headers["location"]).group(1)

    pool = await get_pool()
    lesson_id = await pool.fetchval(
        """SELECT l.lesson_id FROM learning_lessons l
           JOIN learning_units u ON u.unit_id = l.unit_id
           JOIN learning_modules m ON m.module_id = u.module_id
           WHERE m.path_id = $1 ORDER BY l.position LIMIT 1""",
        int(path_id),
    )
    user_id = await pool.fetchval("SELECT user_id FROM users WHERE email = $1", "xp-lesson@example.com")

    await client.post(f"/learning-paths/{path_id}/lessons/{lesson_id}/toggle")  # complete: +15
    await client.post(f"/learning-paths/{path_id}/lessons/{lesson_id}/toggle")  # uncomplete: +0
    await client.post(f"/learning-paths/{path_id}/lessons/{lesson_id}/toggle")  # complete again: +0 (dedup)

    summary = await get_xp_summary(pool, user_id)
    assert summary["total_xp"] == 15


async def test_taking_a_diagnostic_awards_xp(client, monkeypatch):
    async def fake_generate(prompt: str, temperature: float = 0.0) -> str:
        return """{"questions": [
            {"question": "2+2?", "subtopic": "math", "choices": ["3", "4"], "correct_choice_index": 1}
        ]}"""

    monkeypatch.setattr(assessments_service, "generate", fake_generate)
    monkeypatch.setattr("app.assessments.router.llm_is_configured", lambda: True)

    await signup(client, "xp-diagnostic@example.com")
    await client.post("/assessments/start", data={"topic": "Math"})
    await client.post("/assessments/submit", data={"answer_0": "1"})

    pool = await get_pool()
    user_id = await pool.fetchval("SELECT user_id FROM users WHERE email = $1", "xp-diagnostic@example.com")
    summary = await get_xp_summary(pool, user_id)
    assert summary["total_xp"] == 20


async def test_badges_reflect_real_activity_counts():
    pool = await get_pool()
    user_id = await create_user(pool, "badges@example.com", "testpassword123", "Test")

    badges_before = await get_badges(pool, user_id, streak_days=0)
    assert all(not b["earned"] for b in badges_before)

    q = await create_question(pool, user_id, {"question": "Q", "answer": "A", "topic": "t"})
    await record_attempt(pool, user_id, q, rating=3)

    badges_after = await get_badges(pool, user_id, streak_days=7)
    by_id = {b["id"]: b for b in badges_after}
    assert by_id["first_steps"]["earned"] is True
    assert by_id["on_a_roll"]["earned"] is True  # streak_days=7 passed in
    assert by_id["half_century"]["earned"] is False  # only 1 attempt
    assert by_id["path_finisher"]["earned"] is False


async def test_completing_every_lesson_in_a_path_earns_the_path_finisher_badge():
    pool = await get_pool()
    user_id = await create_user(pool, "badges-path@example.com", "testpassword123", "Test")
    path_id = await create_path(pool, user_id, "Learn origami", ai_available=False)

    lesson_ids = await pool.fetch(
        """SELECT l.lesson_id FROM learning_lessons l
           JOIN learning_units u ON u.unit_id = l.unit_id
           JOIN learning_modules m ON m.module_id = u.module_id
           WHERE m.path_id = $1""",
        path_id,
    )
    from app.learning_paths.service import toggle_lesson

    for row in lesson_ids:
        await toggle_lesson(pool, user_id, path_id, row["lesson_id"])

    badges = await get_badges(pool, user_id, streak_days=0)
    by_id = {b["id"]: b for b in badges}
    assert by_id["path_finisher"]["earned"] is True


async def test_topbar_shows_xp_badge_only_once_xp_exists(client):
    await signup(client, "xp-topbar@example.com")
    before = await client.get("/dashboard")
    assert "topbar-xp" not in before.text

    pool = await get_pool()
    user_id = await pool.fetchval("SELECT user_id FROM users WHERE email = $1", "xp-topbar@example.com")
    q = await create_question(pool, user_id, {"question": "Q", "answer": "A", "topic": "t"})
    await record_attempt(pool, user_id, q, rating=5)

    after = await client.get("/dashboard")
    assert "topbar-xp" in after.text
    assert "Lv 1" in after.text
