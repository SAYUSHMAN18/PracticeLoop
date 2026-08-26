from __future__ import annotations

from app.auth.service import get_user_by_email
from app.core.db import get_pool
from app.jobs import gap_analysis
from app.practice.service import create_question, record_attempt
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


async def _run_with_fake_skills(pool, user_id: int, skills: list[str]) -> list[dict]:
    async def fake_extract(jd_text: str) -> list[str]:
        return skills

    original = gap_analysis.extract_skills_from_jd
    gap_analysis.extract_skills_from_jd = fake_extract
    try:
        return await gap_analysis.analyze_gap(pool, user_id, "irrelevant jd text")
    finally:
        gap_analysis.extract_skills_from_jd = original
