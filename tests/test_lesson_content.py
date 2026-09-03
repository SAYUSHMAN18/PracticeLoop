import re

from app.core.db import get_pool
from app.learning_paths import service
from tests.conftest import signup


def _path_id_from_redirect(response) -> str:
    match = re.search(r"/learning-paths/(\d+)", str(response.headers["location"]))
    assert match, response.headers["location"]
    return match.group(1)


async def _create_path(client, email: str, goal: str) -> tuple[str, list[int]]:
    """Signs up, creates a (fallback-skeleton) path, and returns its id
    plus its lesson ids in reading order."""
    await signup(client, email)
    create = await client.post("/learning-paths", data={"goal": goal}, follow_redirects=False)
    path_id = _path_id_from_redirect(create)

    pool = await get_pool()
    rows = await pool.fetch(
        """SELECT l.lesson_id FROM learning_lessons l
           JOIN learning_units u ON u.unit_id = l.unit_id
           JOIN learning_modules m ON m.module_id = u.module_id
           WHERE m.path_id = $1 ORDER BY m.position, u.position, l.position""",
        int(path_id),
    )
    return path_id, [r["lesson_id"] for r in rows]


async def test_opening_a_lesson_generates_fallback_content_without_ai(client):
    path_id, lesson_ids = await _create_path(client, "lesson-fallback@example.com", "Learn origami")
    response = await client.get(f"/learning-paths/{path_id}/lessons/{lesson_ids[0]}")
    assert response.status_code == 200
    assert "Get oriented with the topic" in response.text  # the lesson's own title, in the breadcrumb/heading
    assert "main idea of" in response.text  # the fallback checkpoint question


async def test_lesson_content_is_cached_not_regenerated_on_each_view(client):
    path_id, lesson_ids = await _create_path(client, "lesson-cache@example.com", "Learn origami")
    lesson_id = lesson_ids[0]

    pool = await get_pool()
    await client.get(f"/learning-paths/{path_id}/lessons/{lesson_id}")
    first_content = await pool.fetchval(
        "SELECT content FROM learning_lessons WHERE lesson_id = $1", lesson_id
    )
    assert first_content is not None

    await client.get(f"/learning-paths/{path_id}/lessons/{lesson_id}")
    second_content = await pool.fetchval(
        "SELECT content FROM learning_lessons WHERE lesson_id = $1", lesson_id
    )
    assert second_content == first_content


async def test_ai_generated_content_is_used_when_available(client, monkeypatch):
    async def fake_generate(prompt: str, temperature: float = 0.0, **_: object) -> str:
        return """{
          "concept": "A hash map stores key-value pairs for fast lookup.",
          "example": "dict['x'] = 1 in Python.",
          "checkpoint_question": "What is the average lookup time?",
          "checkpoint_answer": "O(1) on average.",
          "summary": "Hash maps trade memory for speed."
        }"""

    monkeypatch.setattr(service, "generate", fake_generate)
    monkeypatch.setattr("app.learning_paths.router.llm_is_configured", lambda: True)

    path_id, lesson_ids = await _create_path(client, "lesson-ai@example.com", "Learn data structures")
    response = await client.get(f"/learning-paths/{path_id}/lessons/{lesson_ids[0]}")
    assert "A hash map stores key-value pairs" in response.text
    assert "O(1) on average." in response.text


async def test_a_multiline_code_example_keeps_its_newlines_and_indentation(client, monkeypatch):
    # Regression test: the example used to render inside a plain <p>, which
    # collapses every newline and indent the moment the browser lays it
    # out -- a multi-line code sample came out as one run-on line. It now
    # renders inside <pre class="code-block">, which preserves whitespace.
    async def fake_generate(prompt: str, temperature: float = 0.0, **_: object) -> str:
        return """{
          "concept": "Python uses indentation to define blocks.",
          "example": "def greet(name):\\n    if name:\\n        print(name)",
          "checkpoint_question": "What starts a new block?",
          "checkpoint_answer": "A colon, followed by an indented line.",
          "summary": "Indentation is structural in Python."
        }"""

    monkeypatch.setattr(service, "generate", fake_generate)
    monkeypatch.setattr("app.learning_paths.router.llm_is_configured", lambda: True)

    path_id, lesson_ids = await _create_path(client, "lesson-codeblock@example.com", "Learn Python")
    response = await client.get(f"/learning-paths/{path_id}/lessons/{lesson_ids[0]}")
    expected = '<pre class="code-block">def greet(name):\n    if name:\n        print(name)</pre>'
    assert expected in response.text


async def test_prev_next_navigation_across_lesson_boundaries(client):
    path_id, lesson_ids = await _create_path(client, "lesson-nav@example.com", "Learn origami")
    assert len(lesson_ids) == 4  # the fallback skeleton's one unit has exactly 4 lessons

    first = await client.get(f"/learning-paths/{path_id}/lessons/{lesson_ids[0]}")
    assert "Previous" not in first.text
    assert "Next" in first.text

    middle = await client.get(f"/learning-paths/{path_id}/lessons/{lesson_ids[1]}")
    assert "Previous" in middle.text
    assert "Next" in middle.text

    last = await client.get(f"/learning-paths/{path_id}/lessons/{lesson_ids[-1]}")
    assert "Previous" in last.text
    assert "Next" not in last.text


async def test_toggling_from_the_lesson_page_redirects_back_to_it(client):
    path_id, lesson_ids = await _create_path(client, "lesson-toggle-redirect@example.com", "Learn origami")
    lesson_id = lesson_ids[0]

    response = await client.post(
        f"/learning-paths/{path_id}/lessons/{lesson_id}/toggle",
        data={"next": f"/learning-paths/{path_id}/lessons/{lesson_id}"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == f"/learning-paths/{path_id}/lessons/{lesson_id}"


async def test_a_next_value_outside_the_path_is_ignored(client):
    """Guards the open-redirect check in the toggle route -- a `next`
    that isn't this exact path's own URL space falls back to the path
    page instead of being honored."""
    path_id, lesson_ids = await _create_path(client, "lesson-toggle-badnext@example.com", "Learn origami")
    lesson_id = lesson_ids[0]

    response = await client.post(
        f"/learning-paths/{path_id}/lessons/{lesson_id}/toggle",
        data={"next": "https://evil.example.com/steal"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == f"/learning-paths/{path_id}"


async def test_resume_button_points_to_first_incomplete_then_last_once_done(client):
    path_id, lesson_ids = await _create_path(client, "lesson-resume@example.com", "Learn origami")

    index = await client.get(f"/learning-paths/{path_id}")
    assert f"/learning-paths/{path_id}/lessons/{lesson_ids[0]}" in index.text
    assert "Resume" in index.text

    for lesson_id in lesson_ids:
        await client.post(f"/learning-paths/{path_id}/lessons/{lesson_id}/toggle")

    index_done = await client.get(f"/learning-paths/{path_id}")
    assert f"/learning-paths/{path_id}/lessons/{lesson_ids[-1]}" in index_done.text
    assert "Review path" in index_done.text


async def test_another_users_lesson_404s():
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    transport = ASGITransport(app=app)
    owner = AsyncClient(transport=transport, base_url="http://test", follow_redirects=False)
    attacker = AsyncClient(transport=transport, base_url="http://test", follow_redirects=False)

    path_id, lesson_ids = await _create_path(owner, "lesson-victim@example.com", "Victim's path")
    await signup(attacker, "lesson-attacker@example.com")

    response = await attacker.get(f"/learning-paths/{path_id}/lessons/{lesson_ids[0]}")
    assert response.status_code == 404

    await owner.aclose()
    await attacker.aclose()
