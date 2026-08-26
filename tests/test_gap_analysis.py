from __future__ import annotations

from app.auth.service import get_user_by_email
from app.core.config import settings
from app.core.db import get_pool
from app.jobs import gap_analysis
from app.practice.service import create_question, list_questions, record_attempt
from tests.conftest import signup


async def _make_user_with_resume(client, email: str, resume_text: str):
    await signup(client, email)
    pool = await get_pool()
    user = await get_user_by_email(pool, email)
    await pool.execute(
        "UPDATE profiles SET resume_text = $2 WHERE user_id = $1", user["user_id"], resume_text
    )
    return user["user_id"]


async def test_skill_on_resume_and_recalled_is_proven(client):
    user_id = await _make_user_with_resume(
        client, "proven@example.com", "Experienced with Python and FastAPI"
    )
    pool = await get_pool()
    question_id = await create_question(
        pool, user_id, {"question": "Explain the GIL", "topic": "Python", "answer": ""}
    )
    await record_attempt(pool, user_id, question_id, rating=5)

    results = await _run_with_fake_skills(pool, user_id, ["Python"])
    assert results[0]["bucket"] == "proven"


async def test_skill_on_resume_but_never_practiced_is_untested(client):
    user_id = await _make_user_with_resume(client, "untested@example.com", "Experienced with Kubernetes")
    pool = await get_pool()

    results = await _run_with_fake_skills(pool, user_id, ["Kubernetes"])
    assert results[0]["bucket"] == "untested"


async def test_skill_practiced_but_recalled_poorly_is_untested_not_proven(client):
    """On the resume, a matching question exists, but the confidence rating
    was low -- a single blackout-rated attempt must not count as recall
    just because a matching question happens to exist."""
    user_id = await _make_user_with_resume(client, "weak-recall@example.com", "Experienced with Redis")
    pool = await get_pool()
    question_id = await create_question(
        pool, user_id, {"question": "What is Redis?", "topic": "Redis", "answer": ""}
    )
    await record_attempt(pool, user_id, question_id, rating=1)  # blackout

    results = await _run_with_fake_skills(pool, user_id, ["Redis"])
    assert results[0]["bucket"] == "untested"


async def test_skill_not_on_resume_is_missing(client):
    user_id = await _make_user_with_resume(client, "missing@example.com", "Experienced with Java")
    pool = await get_pool()

    results = await _run_with_fake_skills(pool, user_id, ["Rust"])
    assert results[0]["bucket"] == "missing"


async def test_gap_analysis_endpoint_persists_and_redirects(client, monkeypatch):
    async def fake_extract(jd_text: str) -> list[str]:
        return ["Docker"]

    monkeypatch.setattr(gap_analysis, "extract_skills_from_jd", fake_extract)

    await signup(client, "endpoint@example.com")
    response = await client.post("/jobs/gap-analysis", data={"jd_text": "We need a Docker expert."})
    assert response.status_code == 303

    pool = await get_pool()
    user = await get_user_by_email(pool, "endpoint@example.com")
    gaps = await gap_analysis.list_recent_gaps(pool, user["user_id"])
    assert len(gaps) == 1
    assert gaps[0]["skill"] == "Docker"


async def test_gap_analysis_llm_failure_shows_a_clear_error_not_a_500(client, monkeypatch):
    async def failing_extract(jd_text: str) -> list[str]:
        raise RuntimeError("LLM provider unavailable")

    monkeypatch.setattr(gap_analysis, "extract_skills_from_jd", failing_extract)

    await signup(client, "llm-fail@example.com")
    response = await client.post("/jobs/gap-analysis", data={"jd_text": "Anything"})
    assert response.status_code == 502
    assert "Couldn&#39;t analyze" in response.text or "Couldn't analyze" in response.text


async def test_generate_deck_creates_a_question_for_a_missing_skill(client, monkeypatch):
    async def fake_generate_study_card(pool, user_id, topic, difficulty="medium"):
        return await create_question(
            pool, user_id, {"question": f"About {topic}", "topic": topic, "answer": ""}
        )

    monkeypatch.setattr(gap_analysis, "generate_study_card", fake_generate_study_card)

    await signup(client, "deck@example.com")
    pool = await get_pool()
    user = await get_user_by_email(pool, "deck@example.com")
    gap_id = await pool.fetchval(
        """INSERT INTO job_skill_gaps (user_id, skill, bucket, evidence)
           VALUES ($1, 'GraphQL', 'missing', 'not on resume') RETURNING gap_id""",
        user["user_id"],
    )

    result = await gap_analysis.generate_deck_from_gaps(pool, user["user_id"], [gap_id])
    assert result == {"generated": 1, "skipped_existing": 0, "budget_exhausted": False}

    questions = await list_questions(pool, user["user_id"])
    assert any("GraphQL" in q["topic"] for q in questions)


async def test_generate_deck_skips_a_skill_with_an_existing_close_match(client, monkeypatch):
    async def fake_generate_study_card(pool, user_id, topic, difficulty="medium"):
        raise AssertionError("must not generate -- a close match already exists")

    monkeypatch.setattr(gap_analysis, "generate_study_card", fake_generate_study_card)

    await signup(client, "dedup@example.com")
    pool = await get_pool()
    user = await get_user_by_email(pool, "dedup@example.com")
    await create_question(
        pool, user["user_id"], {"question": "What is a Docker container?", "topic": "Docker", "answer": ""}
    )
    gap_id = await pool.fetchval(
        """INSERT INTO job_skill_gaps (user_id, skill, bucket, evidence)
           VALUES ($1, 'Docker', 'missing', 'not on resume') RETURNING gap_id""",
        user["user_id"],
    )

    result = await gap_analysis.generate_deck_from_gaps(pool, user["user_id"], [gap_id])
    assert result == {"generated": 0, "skipped_existing": 1, "budget_exhausted": False}


async def test_generate_deck_ignores_proven_gaps(client, monkeypatch):
    async def fake_generate_study_card(pool, user_id, topic, difficulty="medium"):
        raise AssertionError("must not generate for an already-proven skill")

    monkeypatch.setattr(gap_analysis, "generate_study_card", fake_generate_study_card)

    await signup(client, "proven-skip@example.com")
    pool = await get_pool()
    user = await get_user_by_email(pool, "proven-skip@example.com")
    gap_id = await pool.fetchval(
        """INSERT INTO job_skill_gaps (user_id, skill, bucket, evidence)
           VALUES ($1, 'SQL', 'proven', 'recalled') RETURNING gap_id""",
        user["user_id"],
    )

    result = await gap_analysis.generate_deck_from_gaps(pool, user["user_id"], [gap_id])
    assert result == {"generated": 0, "skipped_existing": 0, "budget_exhausted": False}


async def test_generate_deck_stops_when_budget_runs_out(client, monkeypatch):
    async def fake_generate_study_card(pool, user_id, topic, difficulty="medium"):
        return await create_question(pool, user_id, {"question": topic, "topic": topic, "answer": ""})

    monkeypatch.setattr(gap_analysis, "generate_study_card", fake_generate_study_card)
    monkeypatch.setattr(settings, "llm_daily_budget", 1)

    await signup(client, "budget-deck@example.com")
    pool = await get_pool()
    user = await get_user_by_email(pool, "budget-deck@example.com")
    gap_ids = []
    for skill in ["Terraform", "Kafka"]:
        gap_id = await pool.fetchval(
            """INSERT INTO job_skill_gaps (user_id, skill, bucket, evidence)
               VALUES ($1, $2, 'missing', 'not on resume') RETURNING gap_id""",
            user["user_id"],
            skill,
        )
        gap_ids.append(gap_id)

    result = await gap_analysis.generate_deck_from_gaps(pool, user["user_id"], gap_ids)
    assert result["generated"] == 1
    assert result["budget_exhausted"] is True


async def _run_with_fake_skills(pool, user_id: int, skills: list[str]) -> list[dict]:
    async def fake_extract(jd_text: str) -> list[str]:
        return skills

    original = gap_analysis.extract_skills_from_jd
    gap_analysis.extract_skills_from_jd = fake_extract
    try:
        return await gap_analysis.analyze_gap(pool, user_id, "irrelevant jd text")
    finally:
        gap_analysis.extract_skills_from_jd = original
