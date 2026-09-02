"""Lessons feed the spaced-repetition queue.

"Spaced repetition is the spine" is a stated design principle -- every
practice format schedules through the same FSRS engine. Lessons were the
gap: a student could finish a 40-lesson path and have nothing in their
review queue from it. Completing a lesson now turns its checkpoint into a
review card.
"""

from __future__ import annotations

import re

from app.core.db import get_pool
from app.learning_paths import service
from tests.conftest import signup

_AI_LESSON = """{
  "concept": "A CDN caches your content at edge locations near users.",
  "example": "Cloudflare serving an image from a nearby city instead of your origin.",
  "checkpoint_question": "Why does a CDN reduce latency?",
  "checkpoint_answer": "It serves content from a location physically closer to the user.",
  "summary": "CDNs trade cache-invalidation complexity for lower latency."
}"""


def _path_id(response) -> str:
    match = re.search(r"/learning-paths/(\d+)", str(response.headers["location"]))
    assert match, response.headers["location"]
    return match.group(1)


async def _fresh_path(client, email: str, *, ai: bool, monkeypatch=None) -> tuple[str, list[int]]:
    if ai:
        assert monkeypatch is not None

        async def fake_generate(prompt: str, temperature: float = 0.0) -> str:
            return _AI_LESSON

        monkeypatch.setattr(service, "generate", fake_generate)
        monkeypatch.setattr("app.learning_paths.router.llm_is_configured", lambda: True)

    await signup(client, email)
    create = await client.post(
        "/learning-paths", data={"goal": "Learn how CDNs work"}, follow_redirects=False
    )
    path_id = _path_id(create)

    pool = await get_pool()
    rows = await pool.fetch(
        """SELECT l.lesson_id FROM learning_lessons l
           JOIN learning_units u ON u.unit_id = l.unit_id
           JOIN learning_modules m ON m.module_id = u.module_id
           WHERE m.path_id = $1 ORDER BY m.position, u.position, l.position""",
        int(path_id),
    )
    return path_id, [r["lesson_id"] for r in rows]


async def _lesson_cards(pool, user_email: str) -> list[dict]:
    return [
        dict(r)
        for r in await pool.fetch(
            """SELECT q.* FROM questions q
               JOIN users u ON u.user_id = q.user_id
               WHERE u.email = $1 AND q.source = 'lesson'""",
            user_email,
        )
    ]


async def _toggle(client, path_id, lesson_id):
    return await client.post(
        f"/learning-paths/{path_id}/lessons/{lesson_id}/toggle",
        data={"next": f"/learning-paths/{path_id}"},
    )


async def _open(client, path_id, lesson_id):
    return await client.get(f"/learning-paths/{path_id}/lessons/{lesson_id}")


async def test_completing_an_ai_lesson_creates_a_gradeable_review_card(client, monkeypatch):
    email = "lrc-ai@example.com"
    path_id, lessons = await _fresh_path(client, email, ai=True, monkeypatch=monkeypatch)
    await _open(client, path_id, lessons[0])  # generate content

    await _toggle(client, path_id, lessons[0])

    pool = await get_pool()
    cards = await _lesson_cards(pool, email)
    assert len(cards) == 1
    card = cards[0]
    assert card["question"] == "Why does a CDN reduce latency?"
    assert card["answer"] == "It serves content from a location physically closer to the user."
    assert card["source_lesson_id"] == lessons[0]
    assert card["question_type"] == "free_text"
    assert card["embedding"] is not None  # searchable like any other question


async def test_the_lesson_card_shows_up_in_the_review_queue(client, monkeypatch):
    path_id, lessons = await _fresh_path(client, "lrc-queue@example.com", ai=True, monkeypatch=monkeypatch)
    await _open(client, path_id, lessons[0])

    before = await client.get("/practice/review")
    assert "Why does a CDN reduce latency?" not in before.text

    await _toggle(client, path_id, lessons[0])

    after = await client.get("/practice/review")
    assert "Why does a CDN reduce latency?" in after.text
    assert "From a lesson" in after.text


async def test_grading_the_lesson_card_schedules_it_through_fsrs(client, monkeypatch):
    path_id, lessons = await _fresh_path(client, "lrc-fsrs@example.com", ai=True, monkeypatch=monkeypatch)
    await _open(client, path_id, lessons[0])
    await _toggle(client, path_id, lessons[0])

    pool = await get_pool()
    card = (await _lesson_cards(pool, "lrc-fsrs@example.com"))[0]

    # LLM not mocked for grading here -> self-rate path, which still runs
    # the FSRS scheduler.
    resp = await client.post(f"/practice/review/{card['question_id']}", data={"rating": "4"})
    assert resp.status_code == 200

    state = await pool.fetchrow(
        "SELECT due, stability FROM card_states WHERE question_id = $1", card["question_id"]
    )
    assert state is not None
    assert state["stability"] is not None  # FSRS actually ran, not just an attempts row


async def test_a_fallback_lesson_still_gets_a_card_just_answerless(client):
    """No LLM drafted a real checkpoint answer, so there's nothing to grade
    against -- but "in your own words, what's the main idea" is still a fine
    recall prompt, and it becomes a self-rated card exactly like any
    hand-captured question with no answer."""
    path_id, lessons = await _fresh_path(client, "lrc-fallback@example.com", ai=False)
    await _open(client, path_id, lessons[0])
    await _toggle(client, path_id, lessons[0])

    pool = await get_pool()
    cards = await _lesson_cards(pool, "lrc-fallback@example.com")
    assert len(cards) == 1
    assert "main idea of" in cards[0]["question"]
    assert cards[0]["answer"] == ""  # the fallback sentinel is not stored as a real answer


async def test_re_completing_a_lesson_does_not_stack_duplicate_cards(client, monkeypatch):
    path_id, lessons = await _fresh_path(client, "lrc-dupe@example.com", ai=True, monkeypatch=monkeypatch)
    await _open(client, path_id, lessons[0])
    pool = await get_pool()

    # Review it so the card is kept across the toggle-off, then re-complete.
    await _toggle(client, path_id, lessons[0])
    card = (await _lesson_cards(pool, "lrc-dupe@example.com"))[0]
    await client.post(f"/practice/review/{card['question_id']}", data={"rating": "3"})

    await _toggle(client, path_id, lessons[0])
    await _toggle(client, path_id, lessons[0])

    assert len(await _lesson_cards(pool, "lrc-dupe@example.com")) == 1


async def test_uncompleting_a_never_reviewed_lesson_removes_its_card(client, monkeypatch):
    path_id, lessons = await _fresh_path(client, "lrc-remove@example.com", ai=True, monkeypatch=monkeypatch)
    await _open(client, path_id, lessons[0])
    pool = await get_pool()

    await _toggle(client, path_id, lessons[0])
    assert len(await _lesson_cards(pool, "lrc-remove@example.com")) == 1

    await _toggle(client, path_id, lessons[0])
    assert await _lesson_cards(pool, "lrc-remove@example.com") == []


async def test_uncompleting_a_reviewed_lesson_keeps_the_card(client, monkeypatch):
    """Once there's real attempt history the card is the student's practice,
    not the lesson's checkbox -- un-ticking a box shouldn't delete it."""
    path_id, lessons = await _fresh_path(client, "lrc-keep@example.com", ai=True, monkeypatch=monkeypatch)
    await _open(client, path_id, lessons[0])
    pool = await get_pool()

    await _toggle(client, path_id, lessons[0])
    card = (await _lesson_cards(pool, "lrc-keep@example.com"))[0]
    await client.post(f"/practice/review/{card['question_id']}", data={"rating": "5"})

    await _toggle(client, path_id, lessons[0])

    kept = await _lesson_cards(pool, "lrc-keep@example.com")
    assert len(kept) == 1
    assert kept[0]["question_id"] == card["question_id"]


async def test_completing_from_the_path_tree_defers_the_card_until_the_lesson_is_opened(client, monkeypatch):
    """A lesson completed from the tree view has no generated checkpoint
    yet. Opening it later generates content and backfills the card."""
    path_id, lessons = await _fresh_path(client, "lrc-tree@example.com", ai=True, monkeypatch=monkeypatch)
    pool = await get_pool()

    # toggle without ever opening the lesson
    await _toggle(client, path_id, lessons[0])
    assert await _lesson_cards(pool, "lrc-tree@example.com") == []

    await _open(client, path_id, lessons[0])  # generates content
    assert len(await _lesson_cards(pool, "lrc-tree@example.com")) == 1


async def test_the_card_is_tagged_with_its_unit(client, monkeypatch):
    path_id, lessons = await _fresh_path(client, "lrc-topic@example.com", ai=True, monkeypatch=monkeypatch)
    await _open(client, path_id, lessons[0])
    await _toggle(client, path_id, lessons[0])

    pool = await get_pool()
    card = (await _lesson_cards(pool, "lrc-topic@example.com"))[0]
    unit_title = await pool.fetchval(
        """SELECT u.title FROM learning_units u
           JOIN learning_lessons l ON l.unit_id = u.unit_id
           WHERE l.lesson_id = $1""",
        lessons[0],
    )
    assert card["topic"] == unit_title


async def test_deleting_the_path_removes_lesson_cards_but_not_hand_captured_ones(client, monkeypatch):
    path_id, lessons = await _fresh_path(client, "lrc-delete@example.com", ai=True, monkeypatch=monkeypatch)
    await _open(client, path_id, lessons[0])
    await _toggle(client, path_id, lessons[0])
    await client.post("/practice", data={"question": "my own question", "answer": "mine", "topic": "meta"})

    pool = await get_pool()
    assert len(await _lesson_cards(pool, "lrc-delete@example.com")) == 1

    await client.post(f"/learning-paths/{path_id}/delete")

    assert await _lesson_cards(pool, "lrc-delete@example.com") == []
    remaining = await pool.fetch(
        """SELECT q.question FROM questions q JOIN users u ON u.user_id = q.user_id
           WHERE u.email = $1""",
        "lrc-delete@example.com",
    )
    assert [r["question"] for r in remaining] == ["my own question"]


async def test_the_lesson_page_reflects_the_cards_state(client, monkeypatch):
    path_id, lessons = await _fresh_path(client, "lrc-page@example.com", ai=True, monkeypatch=monkeypatch)

    before = await _open(client, path_id, lessons[0])
    assert "Mark this lesson complete to add this checkpoint" in before.text

    await _toggle(client, path_id, lessons[0])

    after = await _open(client, path_id, lessons[0])
    assert "In your" in after.text and "review queue" in after.text
    assert "due now" in after.text  # never reviewed yet


async def test_another_users_completion_never_creates_a_card_for_someone_else(client, monkeypatch):
    path_id, lessons = await _fresh_path(client, "lrc-owner@example.com", ai=True, monkeypatch=monkeypatch)
    await _open(client, path_id, lessons[0])
    await _toggle(client, path_id, lessons[0])

    pool = await get_pool()
    owner_id = await pool.fetchval("SELECT user_id FROM users WHERE email = $1", "lrc-owner@example.com")
    card_owner = await pool.fetchval("SELECT user_id FROM questions WHERE source_lesson_id = $1", lessons[0])
    assert card_owner == owner_id


async def test_the_path_page_counts_checkpoints_in_review(client, monkeypatch):
    path_id, lessons = await _fresh_path(client, "lrc-count@example.com", ai=True, monkeypatch=monkeypatch)

    page = await client.get(f"/learning-paths/{path_id}")
    assert "checkpoint" not in page.text.lower() or "in review" not in page.text

    await _open(client, path_id, lessons[0])
    await _toggle(client, path_id, lessons[0])
    await _open(client, path_id, lessons[1])
    await _toggle(client, path_id, lessons[1])

    page = await client.get(f"/learning-paths/{path_id}")
    assert "2 checkpoints in review" in page.text
