import re

from app.core.db import get_pool
from app.learning_paths import service
from tests.conftest import signup


def _path_id_from_redirect(response) -> str:
    match = re.search(r"/learning-paths/(\d+)", str(response.headers["location"]))
    assert match, response.headers["location"]
    return match.group(1)


async def test_create_path_without_ai_uses_the_fallback_skeleton(client):
    """GROQ_API_KEY is unset in the test environment, so this exercises
    the same no-AI path a real deploy with no LLM key configured would
    take -- not a mocked-AI shortcut."""
    await signup(client, "path-fallback@example.com")
    response = await client.post("/learning-paths", data={"goal": "Learn to juggle"}, follow_redirects=False)
    assert response.status_code == 303
    path_id = _path_id_from_redirect(response)

    detail = await client.get(f"/learning-paths/{path_id}")
    assert detail.status_code == 200
    assert "Learn to juggle" in detail.text
    assert "Getting started" in detail.text
    assert "Get oriented with the topic" in detail.text


async def test_empty_goal_is_rejected_without_creating_a_path(client):
    await signup(client, "path-empty@example.com")
    response = await client.post("/learning-paths", data={"goal": "   "})
    assert response.status_code == 400
    assert "working toward" in response.text

    paths_page = await client.get("/learning-paths")
    assert "No paths yet" in paths_page.text


async def test_path_list_shows_progress_and_lesson_count(client):
    await signup(client, "path-list@example.com")
    create = await client.post("/learning-paths", data={"goal": "Learn SQL"}, follow_redirects=False)
    path_id = _path_id_from_redirect(create)

    index = await client.get("/learning-paths")
    assert "4 lessons" in index.text  # the fallback skeleton's one unit has 4 lessons
    assert "0% complete" in index.text

    # completing one of the fallback's four lessons should move the list's
    # progress bar off zero
    pool = await get_pool()
    lesson_id = await pool.fetchval(
        """SELECT l.lesson_id FROM learning_lessons l
           JOIN learning_units u ON u.unit_id = l.unit_id
           JOIN learning_modules m ON m.module_id = u.module_id
           WHERE m.path_id = $1 ORDER BY l.position LIMIT 1""",
        int(path_id),
    )
    await client.post(f"/learning-paths/{path_id}/lessons/{lesson_id}/toggle")

    index_after = await client.get("/learning-paths")
    assert "25% complete" in index_after.text


async def test_toggling_a_lesson_is_idempotent_both_ways(client):
    await signup(client, "path-toggle@example.com")
    create = await client.post("/learning-paths", data={"goal": "Learn chess"}, follow_redirects=False)
    path_id = _path_id_from_redirect(create)

    pool = await get_pool()
    lesson_id = await pool.fetchval(
        """SELECT l.lesson_id FROM learning_lessons l
           JOIN learning_units u ON u.unit_id = l.unit_id
           JOIN learning_modules m ON m.module_id = u.module_id
           WHERE m.path_id = $1 ORDER BY l.position LIMIT 1""",
        int(path_id),
    )

    await client.post(f"/learning-paths/{path_id}/lessons/{lesson_id}/toggle")
    completed = await pool.fetchval(
        "SELECT completed_at FROM learning_lessons WHERE lesson_id = $1", lesson_id
    )
    assert completed is not None

    await client.post(f"/learning-paths/{path_id}/lessons/{lesson_id}/toggle")
    completed_again = await pool.fetchval(
        "SELECT completed_at FROM learning_lessons WHERE lesson_id = $1", lesson_id
    )
    assert completed_again is None


async def test_deleting_a_path_removes_its_modules_units_and_lessons(client):
    await signup(client, "path-delete@example.com")
    create = await client.post("/learning-paths", data={"goal": "Learn pottery"}, follow_redirects=False)
    path_id = _path_id_from_redirect(create)

    pool = await get_pool()
    module_id = await pool.fetchval("SELECT module_id FROM learning_modules WHERE path_id = $1", int(path_id))
    assert module_id is not None

    response = await client.post(f"/learning-paths/{path_id}/delete")
    assert response.status_code == 303

    remaining_path = await pool.fetchval(
        "SELECT path_id FROM learning_paths WHERE path_id = $1", int(path_id)
    )
    remaining_module = await pool.fetchval(
        "SELECT module_id FROM learning_modules WHERE module_id = $1", module_id
    )
    assert remaining_path is None
    assert remaining_module is None


async def test_another_users_path_404s_for_view_delete_and_toggle():
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    transport = ASGITransport(app=app)
    owner = AsyncClient(transport=transport, base_url="http://test", follow_redirects=False)
    attacker = AsyncClient(transport=transport, base_url="http://test", follow_redirects=False)
    await signup(owner, "path-victim@example.com")
    await signup(attacker, "path-attacker@example.com")

    create = await owner.post("/learning-paths", data={"goal": "Victim's path"})
    path_id = _path_id_from_redirect(create)
    pool = await get_pool()
    lesson_id = await pool.fetchval(
        """SELECT l.lesson_id FROM learning_lessons l
           JOIN learning_units u ON u.unit_id = l.unit_id
           JOIN learning_modules m ON m.module_id = u.module_id
           WHERE m.path_id = $1 ORDER BY l.position LIMIT 1""",
        int(path_id),
    )

    assert (await attacker.get(f"/learning-paths/{path_id}")).status_code == 404
    assert (await attacker.post(f"/learning-paths/{path_id}/lessons/{lesson_id}/toggle")).status_code == 404
    assert (await attacker.post(f"/learning-paths/{path_id}/delete")).status_code == 404

    # and the owner's path is untouched by the attacker's attempts
    still_there = await owner.get(f"/learning-paths/{path_id}")
    assert still_there.status_code == 200

    await owner.aclose()
    await attacker.aclose()


async def test_subjects_page_lists_every_template_and_starting_one_creates_a_path(client):
    await signup(client, "path-template@example.com")
    subjects = await client.get("/subjects")
    assert subjects.status_code == 200
    for template in service.TEMPLATES:
        assert template["title"] in subjects.text

    response = await client.post("/subjects/python-job-ready/start", follow_redirects=False)
    assert response.status_code == 303
    path_id = _path_id_from_redirect(response)

    detail = await client.get(f"/learning-paths/{path_id}")
    assert "Become job-ready in Python" in detail.text

    pool = await get_pool()
    source_type = await pool.fetchval(
        "SELECT source_type FROM learning_paths WHERE path_id = $1", int(path_id)
    )
    assert source_type == "template"


async def test_starting_an_unknown_template_404s(client):
    await signup(client, "path-template-404@example.com")
    response = await client.post("/subjects/not-a-real-template/start")
    assert response.status_code == 404


async def test_ai_generated_skeleton_is_used_when_available(client, monkeypatch):
    """With a working LLM, the real generated structure (not the fallback)
    should be what gets persisted."""

    async def fake_generate(prompt: str, temperature: float = 0.0, **_: object) -> str:
        return """{
          "path_title": "Backend Engineering Basics",
          "modules": [
            {
              "title": "HTTP fundamentals",
              "description": "How the web talks to itself.",
              "units": [
                {
                  "title": "Requests and responses",
                  "description": "",
                  "lessons": ["What is an HTTP method?", "Status codes"]
                }
              ]
            }
          ]
        }"""

    monkeypatch.setattr(service, "generate", fake_generate)
    monkeypatch.setattr("app.learning_paths.router.llm_is_configured", lambda: True)

    await signup(client, "path-ai@example.com")
    response = await client.post(
        "/learning-paths", data={"goal": "Backend engineering"}, follow_redirects=False
    )
    path_id = _path_id_from_redirect(response)

    detail = await client.get(f"/learning-paths/{path_id}")
    assert "Backend Engineering Basics" in detail.text
    assert "HTTP fundamentals" in detail.text
    assert "What is an HTTP method?" in detail.text
    assert "Getting started" not in detail.text  # fallback skeleton wasn't used


async def test_a_malformed_ai_response_falls_back_to_the_deterministic_skeleton(client, monkeypatch):
    async def broken_generate(prompt: str, temperature: float = 0.0, **_: object) -> str:
        return "not json at all"

    monkeypatch.setattr(service, "generate", broken_generate)
    monkeypatch.setattr("app.learning_paths.router.llm_is_configured", lambda: True)

    await signup(client, "path-ai-broken@example.com")
    response = await client.post(
        "/learning-paths", data={"goal": "Something obscure"}, follow_redirects=False
    )
    path_id = _path_id_from_redirect(response)

    detail = await client.get(f"/learning-paths/{path_id}")
    assert "Getting started" in detail.text
