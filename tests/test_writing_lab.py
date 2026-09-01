import pytest

from app.labs import writing_service
from tests.conftest import signup


async def test_no_ai_configured_raises_unavailable():
    with pytest.raises(writing_service.WritingFeedbackUnavailable):
        await writing_service.get_feedback("Some essay text.", "essay", ai_available=False)


async def test_empty_text_is_rejected_before_any_ai_call():
    with pytest.raises(writing_service.WritingFeedbackFailed, match="Paste"):
        await writing_service.get_feedback("   ", "essay", ai_available=True)


async def test_feedback_is_parsed_and_scores_clamped(monkeypatch):
    async def fake_generate(prompt: str, temperature: float = 0.0) -> str:
        return """{
          "clarity_score": 9, "structure_score": 0, "grammar_score": 4,
          "strengths": ["Clear thesis", "Good examples"],
          "improvements": ["Tighten the conclusion"],
          "summary": "Solid overall, needs a stronger ending."
        }"""

    monkeypatch.setattr(writing_service, "generate", fake_generate)

    feedback = await writing_service.get_feedback("My essay.", "essay", ai_available=True)
    assert feedback["clarity_score"] == 5  # clamped down from 9
    assert feedback["structure_score"] == 1  # clamped up from 0
    assert feedback["grammar_score"] == 4
    assert feedback["strengths"] == ["Clear thesis", "Good examples"]
    assert feedback["improvements"] == ["Tighten the conclusion"]
    assert "stronger ending" in feedback["summary"]


async def test_malformed_ai_response_raises_failed_not_a_crash(monkeypatch):
    async def broken_generate(prompt: str, temperature: float = 0.0) -> str:
        return "not json"

    monkeypatch.setattr(writing_service, "generate", broken_generate)

    with pytest.raises(writing_service.WritingFeedbackFailed):
        await writing_service.get_feedback("My essay.", "essay", ai_available=True)


async def test_writing_lab_page_renders(client):
    await signup(client, "writinglab-page@example.com")
    response = await client.get("/labs/writing")
    assert response.status_code == 200
    assert "Writing Lab" in response.text


async def test_reviewing_without_ai_shows_an_honest_notice_not_fake_feedback(client):
    await signup(client, "writinglab-noai@example.com")
    response = await client.post(
        "/labs/writing/review", data={"text": "My essay text here.", "kind": "essay"}
    )
    assert response.status_code == 200
    assert "needs an AI provider configured" in response.text


async def test_reviewing_with_ai_shows_the_real_feedback(client, monkeypatch):
    async def fake_generate(prompt: str, temperature: float = 0.0) -> str:
        return """{
          "clarity_score": 4, "structure_score": 3, "grammar_score": 5,
          "strengths": ["Good voice"], "improvements": ["Add a counterargument"],
          "summary": "Nicely argued."
        }"""

    monkeypatch.setattr(writing_service, "generate", fake_generate)
    monkeypatch.setattr("app.labs.router.llm_is_configured", lambda: True)

    await signup(client, "writinglab-ai@example.com")
    response = await client.post(
        "/labs/writing/review", data={"text": "My essay text here.", "kind": "cover_letter"}
    )
    assert response.status_code == 200
    assert "Good voice" in response.text
    assert "Add a counterargument" in response.text
    assert "Nicely argued." in response.text
