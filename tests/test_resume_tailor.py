from __future__ import annotations

from app.auth.service import get_user_by_email
from app.core.db import get_pool
from app.jobs import resume_tailor
from app.jobs import router as jobs_router
from tests.conftest import signup


async def test_fallback_tailor_finds_overlap_and_gaps():
    resume = "Built REST APIs in Python with FastAPI and PostgreSQL."
    jd = "Looking for a Python engineer experienced with FastAPI, Kubernetes, and Kafka messaging"

    result = await resume_tailor.tailor_resume(resume, jd, ai_available=False)

    assert result["ai_used"] is False
    assert result["summary"] is None
    assert result["bullets"] == []
    assert "python" in result["emphasize"]
    assert "fastapi" in result["emphasize"]
    assert "kubernetes" in result["gaps"]
    assert "kafka" in result["gaps"]
    # already-covered ground shouldn't also show up as a gap
    assert "python" not in result["gaps"]


async def test_ai_tailor_failure_degrades_to_fallback_not_a_crash(monkeypatch):
    async def failing_generate(prompt: str, temperature: float = 0.0, **_: object) -> str:
        raise RuntimeError("LLM provider unavailable")

    monkeypatch.setattr(resume_tailor, "generate", failing_generate)

    result = await resume_tailor.tailor_resume(
        "Experienced with Go and Docker.", "Need a Go and Docker engineer.", ai_available=True
    )

    assert result["ai_used"] is False
    assert "go" in result["emphasize"]


async def test_ai_tailor_parses_a_valid_llm_response(monkeypatch):
    async def fake_generate(prompt: str, temperature: float = 0.0, **_: object) -> str:
        return (
            '{"summary": "Backend engineer.", "bullets": ["Shipped the payments API"], '
            '"emphasize": ["Python"], "gaps": ["Rust"]}'
        )

    monkeypatch.setattr(resume_tailor, "generate", fake_generate)

    result = await resume_tailor.tailor_resume("Python resume", "Rust JD", ai_available=True)

    assert result == {
        "ai_used": True,
        "summary": "Backend engineer.",
        "bullets": ["Shipped the payments API"],
        "emphasize": ["Python"],
        "gaps": ["Rust"],
    }


async def test_tailor_resume_endpoint_requires_a_resume_on_file(client):
    await signup(client, "no-resume@example.com")
    response = await client.post("/jobs/tailor-resume", data={"jd_text": "Need a Python engineer."})
    assert response.status_code == 400
    body = response.text
    assert "don&#39;t have a resume on file" in body or "don't have a resume on file" in body


async def test_tailor_resume_endpoint_falls_back_without_an_llm_configured(client, monkeypatch):
    # Force the deterministic path explicitly rather than relying on the
    # ambient environment having no LLM key configured -- locally it does,
    # and this test must behave the same in both places.
    monkeypatch.setattr(jobs_router, "llm_is_configured", lambda: False)

    await signup(client, "tailor-fallback@example.com")
    pool = await get_pool()
    user = await get_user_by_email(pool, "tailor-fallback@example.com")
    await pool.execute(
        "UPDATE profiles SET resume_text = $2 WHERE user_id = $1",
        user["user_id"],
        "Built services in Python with Django.",
    )

    response = await client.post(
        "/jobs/tailor-resume", data={"jd_text": "Need a Python engineer with Django and AWS."}
    )
    assert response.status_code == 200
    assert "python" in response.text.lower()
    assert "aws" in response.text.lower()
