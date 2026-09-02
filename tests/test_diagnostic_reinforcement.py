"""Phase 17: a diagnostic result becomes a focus module in a learning path.

Covers both the no-AI and AI-configured paths, since the whole feature is
supposed to produce a real, checkable plan either way -- the LLM only ever
improves the lesson *titles*, never gates whether a plan exists at all.
"""

import re

from app.assessments import service as assessments_service
from app.core.db import get_pool
from app.learning_paths import service as paths_service
from tests.conftest import signup

_FAKE_REMEDIATION_JSON = """{
  "unit_title": "Closing your division gaps",
  "unit_description": "Targets the two subtopics you missed.",
  "lessons": ["Long division, step by step", "Remainders and what they mean"]
}"""


def _path_id_from_redirect(response) -> int:
    match = re.search(r"/learning-paths/(\d+)", str(response.headers["location"]))
    assert match, response.headers["location"]
    return int(match.group(1))


async def _make_attempt(email: str, topic: str = "Arithmetic", weak: list[str] | None = None) -> int:
    pool = await get_pool()
    user_id = await pool.fetchval("SELECT user_id FROM users WHERE email = $1", email)
    result = await assessments_service.record_attempt(
        pool, user_id, topic, 1, 3, ["long division", "remainders"] if weak is None else weak
    )
    return result["attempt_id"]


async def _modules(path_id: int) -> list:
    pool = await get_pool()
    return await pool.fetch(
        "SELECT title, position, source_attempt_id FROM learning_modules "
        "WHERE path_id = $1 ORDER BY position",
        path_id,
    )


async def _lesson_titles(path_id: int) -> list[str]:
    pool = await get_pool()
    rows = await pool.fetch(
        """SELECT l.title FROM learning_lessons l
           JOIN learning_units u ON u.unit_id = l.unit_id
           JOIN learning_modules m ON m.module_id = u.module_id
           WHERE m.path_id = $1 ORDER BY m.position, u.position, l.position""",
        path_id,
    )
    return [r["title"] for r in rows]


async def test_without_ai_each_weak_subtopic_becomes_its_own_lesson(client):
    """No LLM key is configured in tests, so this is the real no-AI path.
    The gaps came from the student's own wrong answers, so naming a lesson
    after each one is honest -- nothing here is invented."""
    await signup(client, "reinforce-fallback@example.com")
    attempt_id = await _make_attempt("reinforce-fallback@example.com")

    response = await client.post(
        f"/assessments/result/{attempt_id}/reinforce", data={"path_id": ""}, follow_redirects=False
    )
    assert response.status_code == 303
    path_id = _path_id_from_redirect(response)

    assert await _lesson_titles(path_id) == ["Review: long division", "Review: remainders"]

    modules = await _modules(path_id)
    assert len(modules) == 1
    assert modules[0]["title"] == "Focus: Arithmetic"
    assert modules[0]["source_attempt_id"] == attempt_id


async def test_with_ai_the_generated_lesson_titles_are_used(client, monkeypatch):
    async def fake_generate(prompt: str, temperature: float = 0.0) -> str:
        # The prompt must actually carry the measured gaps, or the feature
        # is just "generate a unit about the topic" wearing a diagnostic's
        # name.
        assert "long division" in prompt
        assert "remainders" in prompt
        return _FAKE_REMEDIATION_JSON

    monkeypatch.setattr(paths_service, "generate", fake_generate)
    monkeypatch.setattr("app.assessments.router.llm_is_configured", lambda: True)

    await signup(client, "reinforce-ai@example.com")
    attempt_id = await _make_attempt("reinforce-ai@example.com")

    response = await client.post(
        f"/assessments/result/{attempt_id}/reinforce", data={"path_id": ""}, follow_redirects=False
    )
    path_id = _path_id_from_redirect(response)

    assert await _lesson_titles(path_id) == [
        "Long division, step by step",
        "Remainders and what they mean",
    ]
    detail = await client.get(f"/learning-paths/{path_id}")
    assert "Closing your division gaps" in detail.text


async def test_a_failed_llm_call_falls_back_instead_of_erroring(client, monkeypatch):
    async def exploding_generate(prompt: str, temperature: float = 0.0) -> str:
        raise RuntimeError("provider is down")

    monkeypatch.setattr(paths_service, "generate", exploding_generate)
    monkeypatch.setattr("app.assessments.router.llm_is_configured", lambda: True)

    await signup(client, "reinforce-llm-down@example.com")
    attempt_id = await _make_attempt("reinforce-llm-down@example.com")

    response = await client.post(
        f"/assessments/result/{attempt_id}/reinforce", data={"path_id": ""}, follow_redirects=False
    )
    assert response.status_code == 303
    path_id = _path_id_from_redirect(response)
    assert await _lesson_titles(path_id) == ["Review: long division", "Review: remainders"]


async def test_focus_module_goes_to_the_top_of_an_existing_path(client):
    """Re-planning around a measurement means the measured gap is what you
    study next -- appending it last would bury it under modules the
    diagnostic just showed you don't need yet."""
    await signup(client, "reinforce-existing@example.com")
    create = await client.post("/learning-paths", data={"goal": "Learn maths"}, follow_redirects=False)
    path_id = _path_id_from_redirect(create)

    before = await _modules(path_id)
    assert [m["position"] for m in before] == [0]
    original_title = before[0]["title"]

    attempt_id = await _make_attempt("reinforce-existing@example.com")
    response = await client.post(
        f"/assessments/result/{attempt_id}/reinforce",
        data={"path_id": str(path_id)},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == f"/learning-paths/{path_id}"

    after = await _modules(path_id)
    assert [m["title"] for m in after] == ["Focus: Arithmetic", original_title]
    assert [m["position"] for m in after] == [0, 1]


async def test_submitting_the_same_result_twice_does_not_stack_duplicate_modules(client):
    await signup(client, "reinforce-twice@example.com")
    attempt_id = await _make_attempt("reinforce-twice@example.com")

    first = await client.post(
        f"/assessments/result/{attempt_id}/reinforce", data={"path_id": ""}, follow_redirects=False
    )
    path_id = _path_id_from_redirect(first)

    second = await client.post(
        f"/assessments/result/{attempt_id}/reinforce",
        data={"path_id": str(path_id)},
        follow_redirects=False,
    )
    assert second.status_code == 303

    modules = await _modules(path_id)
    assert len(modules) == 1
    assert await _lesson_titles(path_id) == ["Review: long division", "Review: remainders"]


async def test_a_clean_diagnostic_has_nothing_to_reinforce(client):
    await signup(client, "reinforce-clean@example.com")
    attempt_id = await _make_attempt("reinforce-clean@example.com", weak=[])

    response = await client.post(f"/assessments/result/{attempt_id}/reinforce", data={"path_id": ""})
    assert response.status_code == 400
    # Jinja autoescapes the apostrophe in "didn't", so match around it.
    assert "flag any weak subtopics" in response.text


async def test_result_page_offers_the_form_only_when_there_are_gaps(client):
    await signup(client, "reinforce-form@example.com")
    with_gaps = await _make_attempt("reinforce-form@example.com")
    clean = await _make_attempt("reinforce-form@example.com", weak=[])

    page = await client.get(f"/assessments/result/{with_gaps}")
    assert f"/assessments/result/{with_gaps}/reinforce" in page.text
    assert "Build my focus module" in page.text

    clean_page = await client.get(f"/assessments/result/{clean}")
    assert "Build my focus module" not in clean_page.text


async def test_budget_exhaustion_still_builds_a_plan_rather_than_429ing(client, monkeypatch):
    """The one page whose entire purpose is "here's what to do next" must
    not dead-end on a rate limit -- it degrades to the deterministic unit."""
    from app.core.llm_budget import LLMBudgetExceeded

    async def over_budget(pool, user_id):
        raise LLMBudgetExceeded()

    monkeypatch.setattr("app.assessments.router.consume_llm_budget", over_budget)
    monkeypatch.setattr("app.assessments.router.llm_is_configured", lambda: True)

    await signup(client, "reinforce-budget@example.com")
    attempt_id = await _make_attempt("reinforce-budget@example.com")

    response = await client.post(
        f"/assessments/result/{attempt_id}/reinforce", data={"path_id": ""}, follow_redirects=False
    )
    assert response.status_code == 303
    path_id = _path_id_from_redirect(response)
    assert await _lesson_titles(path_id) == ["Review: long division", "Review: remainders"]


async def test_cannot_reinforce_another_users_diagnostic():
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    transport = ASGITransport(app=app)
    owner = AsyncClient(transport=transport, base_url="http://test")
    attacker = AsyncClient(transport=transport, base_url="http://test")

    await signup(owner, "reinforce-victim@example.com")
    attempt_id = await _make_attempt("reinforce-victim@example.com")

    await signup(attacker, "reinforce-attacker@example.com")
    response = await attacker.post(f"/assessments/result/{attempt_id}/reinforce", data={"path_id": ""})
    assert response.status_code == 404

    await owner.aclose()
    await attacker.aclose()


async def test_cannot_reinforce_into_another_users_path():
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    transport = ASGITransport(app=app)
    owner = AsyncClient(transport=transport, base_url="http://test")
    attacker = AsyncClient(transport=transport, base_url="http://test")

    await signup(owner, "reinforce-path-victim@example.com")
    create = await owner.post("/learning-paths", data={"goal": "Private path"}, follow_redirects=False)
    victim_path_id = _path_id_from_redirect(create)

    await signup(attacker, "reinforce-path-attacker@example.com")
    attacker_attempt = await _make_attempt("reinforce-path-attacker@example.com")

    response = await attacker.post(
        f"/assessments/result/{attacker_attempt}/reinforce", data={"path_id": str(victim_path_id)}
    )
    assert response.status_code == 404

    # and the victim's path is untouched
    modules = await _modules(victim_path_id)
    assert all(m["source_attempt_id"] is None for m in modules)

    await owner.aclose()
    await attacker.aclose()
