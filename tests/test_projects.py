import re

from app.core.db import get_pool
from app.gamification.service import get_xp_summary
from app.projects import service
from tests.conftest import signup


def _project_id_from_redirect(response) -> str:
    match = re.search(r"/projects/(\d+)", str(response.headers["location"]))
    assert match, response.headers["location"]
    return match.group(1)


async def test_empty_topic_is_rejected(client):
    await signup(client, "project-empty@example.com")
    response = await client.post("/projects", data={"topic": "   "})
    assert response.status_code == 400
    assert "kind of project" in response.text


async def test_creating_a_project_without_ai_uses_the_fallback_idea(client):
    await signup(client, "project-fallback@example.com")
    response = await client.post("/projects", data={"topic": "REST APIs"}, follow_redirects=False)
    assert response.status_code == 303
    project_id = _project_id_from_redirect(response)

    detail = await client.get(f"/projects/{project_id}")
    assert detail.status_code == 200
    assert "REST APIs" in detail.text
    assert (
        "Build a first working version" in detail.text
    )  # a fallback milestone (avoids the apostrophe in "you're")


async def test_toggling_a_milestone_awards_xp_once_even_if_repeated(client):
    await signup(client, "project-milestone-xp@example.com")
    create = await client.post("/projects", data={"topic": "REST APIs"}, follow_redirects=False)
    project_id = _project_id_from_redirect(create)

    pool = await get_pool()
    milestone_id = await pool.fetchval(
        "SELECT milestone_id FROM project_milestones WHERE project_id = $1 ORDER BY position LIMIT 1",
        int(project_id),
    )
    user_id = await pool.fetchval(
        "SELECT user_id FROM users WHERE email = $1", "project-milestone-xp@example.com"
    )

    await client.post(f"/projects/{project_id}/milestones/{milestone_id}/toggle")  # complete: +8
    await client.post(f"/projects/{project_id}/milestones/{milestone_id}/toggle")  # uncomplete: +0
    await client.post(f"/projects/{project_id}/milestones/{milestone_id}/toggle")  # complete again: +0

    summary = await get_xp_summary(pool, user_id)
    assert summary["total_xp"] == 8


async def test_submitting_without_ai_shows_an_honest_notice_and_awards_xp(client):
    await signup(client, "project-submit-noai@example.com")
    create = await client.post("/projects", data={"topic": "REST APIs"}, follow_redirects=False)
    project_id = _project_id_from_redirect(create)

    response = await client.post(
        f"/projects/{project_id}/submit",
        data={"submission_text": "I built a Flask API with three endpoints.", "submission_link": ""},
        follow_redirects=False,
    )
    assert response.status_code == 303

    detail = await client.get(f"/projects/{project_id}")
    assert "Submitted" in detail.text
    assert "No AI feedback was available" in detail.text
    assert "I built a Flask API" in detail.text

    pool = await get_pool()
    user_id = await pool.fetchval(
        "SELECT user_id FROM users WHERE email = $1", "project-submit-noai@example.com"
    )
    summary = await get_xp_summary(pool, user_id)
    assert summary["total_xp"] == 30


async def test_resubmitting_does_not_double_award_xp(client):
    await signup(client, "project-resubmit@example.com")
    create = await client.post("/projects", data={"topic": "REST APIs"}, follow_redirects=False)
    project_id = _project_id_from_redirect(create)

    await client.post(
        f"/projects/{project_id}/submit", data={"submission_text": "First draft.", "submission_link": ""}
    )
    await client.post(
        f"/projects/{project_id}/submit", data={"submission_text": "Revised draft.", "submission_link": ""}
    )

    pool = await get_pool()
    user_id = await pool.fetchval(
        "SELECT user_id FROM users WHERE email = $1", "project-resubmit@example.com"
    )
    summary = await get_xp_summary(pool, user_id)
    assert summary["total_xp"] == 30  # not 60

    detail = await client.get(f"/projects/{project_id}")
    assert "Revised draft." in detail.text


async def test_empty_submission_is_rejected(client):
    await signup(client, "project-empty-submit@example.com")
    create = await client.post("/projects", data={"topic": "REST APIs"}, follow_redirects=False)
    project_id = _project_id_from_redirect(create)

    response = await client.post(
        f"/projects/{project_id}/submit", data={"submission_text": "   ", "submission_link": ""}
    )
    assert response.status_code == 400


async def test_deleting_a_project_removes_its_milestones(client):
    await signup(client, "project-delete@example.com")
    create = await client.post("/projects", data={"topic": "REST APIs"}, follow_redirects=False)
    project_id = _project_id_from_redirect(create)

    pool = await get_pool()
    milestone_id = await pool.fetchval(
        "SELECT milestone_id FROM project_milestones WHERE project_id = $1 LIMIT 1", int(project_id)
    )

    response = await client.post(f"/projects/{project_id}/delete")
    assert response.status_code == 303

    remaining_project = await pool.fetchval(
        "SELECT project_id FROM projects WHERE project_id = $1", int(project_id)
    )
    remaining_milestone = await pool.fetchval(
        "SELECT milestone_id FROM project_milestones WHERE milestone_id = $1", milestone_id
    )
    assert remaining_project is None
    assert remaining_milestone is None


async def test_a_path_id_belonging_to_another_user_is_silently_dropped():
    """create_project's path_id ownership check -- attaching a project to
    someone else's learning path must never succeed, silently or not."""
    from app.auth.service import create_user
    from app.learning_paths.service import create_path

    pool = await get_pool()
    owner_id = await create_user(pool, "project-path-owner@example.com", "testpassword123", "Test")
    attacker_id = await create_user(pool, "project-path-attacker@example.com", "testpassword123", "Test")
    path_id = await create_path(pool, owner_id, "Owner's path", ai_available=False)

    project_id = await service.create_project(
        pool, attacker_id, "My project", "A brief", ["Step 1"], path_id=path_id
    )
    project = await pool.fetchrow("SELECT path_id FROM projects WHERE project_id = $1", project_id)
    assert project["path_id"] is None


async def test_another_users_project_404s_on_view_toggle_submit_and_delete():
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    transport = ASGITransport(app=app)
    owner = AsyncClient(transport=transport, base_url="http://test", follow_redirects=False)
    attacker = AsyncClient(transport=transport, base_url="http://test", follow_redirects=False)

    await signup(owner, "project-victim@example.com")
    create = await owner.post("/projects", data={"topic": "Victim's project"})
    project_id = _project_id_from_redirect(create)
    pool = await get_pool()
    milestone_id = await pool.fetchval(
        "SELECT milestone_id FROM project_milestones WHERE project_id = $1 LIMIT 1", int(project_id)
    )

    await signup(attacker, "project-attacker@example.com")
    assert (await attacker.get(f"/projects/{project_id}")).status_code == 404
    assert (
        await attacker.post(f"/projects/{project_id}/milestones/{milestone_id}/toggle")
    ).status_code == 404
    assert (
        await attacker.post(f"/projects/{project_id}/submit", data={"submission_text": "hijacked"})
    ).status_code == 404
    assert (await attacker.post(f"/projects/{project_id}/delete")).status_code == 404

    await owner.aclose()
    await attacker.aclose()


async def test_ai_generated_idea_is_used_when_available(client, monkeypatch):
    async def fake_generate(prompt: str, temperature: float = 0.0, **_: object) -> str:
        return """{
          "title": "Build a URL shortener",
          "brief": "A small service that shortens URLs and redirects visitors.",
          "milestones": ["Design the schema", "Build the redirect endpoint", "Add basic auth"]
        }"""

    monkeypatch.setattr(service, "generate", fake_generate)
    monkeypatch.setattr("app.projects.router.llm_is_configured", lambda: True)

    await signup(client, "project-ai@example.com")
    response = await client.post("/projects", data={"topic": "backend engineering"}, follow_redirects=False)
    project_id = _project_id_from_redirect(response)

    detail = await client.get(f"/projects/{project_id}")
    assert "Build a URL shortener" in detail.text
    assert "Design the schema" in detail.text


async def test_malformed_ai_idea_falls_back_to_the_deterministic_idea(client, monkeypatch):
    async def broken_generate(prompt: str, temperature: float = 0.0, **_: object) -> str:
        return "not json"

    monkeypatch.setattr(service, "generate", broken_generate)
    monkeypatch.setattr("app.projects.router.llm_is_configured", lambda: True)

    await signup(client, "project-ai-broken@example.com")
    response = await client.post("/projects", data={"topic": "networking"}, follow_redirects=False)
    project_id = _project_id_from_redirect(response)

    detail = await client.get(f"/projects/{project_id}")
    assert (
        "Build a first working version" in detail.text
    )  # a fallback milestone (avoids the apostrophe in "you're")


async def test_ai_feedback_is_shown_when_available(client, monkeypatch):
    async def fake_generate(prompt: str, temperature: float = 0.0, **_: object) -> str:
        return """{
          "completeness_score": 4, "quality_score": 5,
          "strengths": ["Clean structure"], "improvements": ["Add tests"],
          "summary": "Well done overall."
        }"""

    monkeypatch.setattr(service, "generate", fake_generate)
    monkeypatch.setattr("app.projects.router.llm_is_configured", lambda: True)

    await signup(client, "project-feedback@example.com")
    create = await client.post("/projects", data={"topic": "testing"}, follow_redirects=False)
    project_id = _project_id_from_redirect(create)

    await client.post(
        f"/projects/{project_id}/submit",
        data={"submission_text": "Here is my project.", "submission_link": ""},
    )
    detail = await client.get(f"/projects/{project_id}")
    assert "Clean structure" in detail.text
    assert "Add tests" in detail.text
    assert "Well done overall." in detail.text


async def test_portfolio_aggregates_submitted_projects_and_badges(client, monkeypatch):
    monkeypatch.setattr("app.projects.router.llm_is_configured", lambda: False)

    await signup(client, "portfolio-user@example.com")
    create = await client.post("/projects", data={"topic": "REST APIs"}, follow_redirects=False)
    project_id = _project_id_from_redirect(create)
    await client.post(
        f"/projects/{project_id}/submit", data={"submission_text": "Done.", "submission_link": ""}
    )

    response = await client.get("/portfolio")
    assert response.status_code == 200
    assert "REST APIs" in response.text
    assert "1" in response.text  # at least "1" project submitted somewhere in the stat grid
