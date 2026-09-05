import pytest

from app.core.config import settings
from app.core.db import get_pool
from app.learning_paths.service import create_path
from app.mentor import service
from tests.conftest import signup


async def test_conversation_loads_with_no_ai_configured_notice(client):
    await signup(client, "mentor-noai@example.com")
    response = await client.get("/mentor/conversation")
    assert response.status_code == 200
    assert "needs an AI provider configured" in response.text


async def test_sending_a_message_without_ai_gets_an_honest_reply_not_a_crash(client):
    await signup(client, "mentor-noai-msg@example.com")
    response = await client.post("/mentor/message", data={"text": "Hello?", "context_type": "general"})
    assert response.status_code == 200
    assert "Hello?" in response.text  # the student's own message is shown
    assert "needs an AI provider configured" in response.text
    # A canned fallback reply isn't actually AI-generated -- the
    # disclaimer that appears on real LLM output must not appear here.
    assert "AI-generated" not in response.text


async def test_blank_message_is_ignored(client):
    await signup(client, "mentor-blank@example.com")
    response = await client.post("/mentor/message", data={"text": "   ", "context_type": "general"})
    assert response.status_code == 200

    pool = await get_pool()
    user_id = await pool.fetchval("SELECT user_id FROM users WHERE email = $1", "mentor-blank@example.com")
    count = await pool.fetchval(
        "SELECT count(*) FROM mentor_messages m JOIN mentor_conversations c "
        "ON c.conversation_id = m.conversation_id WHERE c.user_id = $1",
        user_id,
    )
    assert count == 0


async def test_get_or_create_conversation_is_idempotent_per_context():
    pool = await get_pool()
    from app.auth.service import create_user

    user_id = await create_user(pool, "mentor-idempotent@example.com", "testpassword123", "Test")

    first = await service.get_or_create_conversation(pool, user_id, "general", None)
    second = await service.get_or_create_conversation(pool, user_id, "general", None)
    assert first == second

    lesson_convo = await service.get_or_create_conversation(pool, user_id, "lesson", 999)
    assert lesson_convo != first


async def test_quick_action_sends_its_canned_text(client):
    await signup(client, "mentor-quickaction@example.com")
    response = await client.post(
        "/mentor/quick-action", data={"action_id": "give_hint", "context_type": "general"}
    )
    assert response.status_code == 200
    assert service.QUICK_ACTIONS["give_hint"] in response.text


async def test_unknown_quick_action_404s(client):
    await signup(client, "mentor-badaction@example.com")
    response = await client.post("/mentor/quick-action", data={"action_id": "not-a-real-action"})
    assert response.status_code == 404


async def test_invalid_context_type_falls_back_to_general(client):
    await signup(client, "mentor-badcontext@example.com")
    response = await client.get("/mentor/conversation?context_type=nonsense")
    assert response.status_code == 200  # no crash, silently treated as general


async def test_lesson_context_id_belonging_to_another_user_falls_back_to_general():
    """build_context is ownership-checked -- a context_id the caller
    doesn't own must never leak that lesson's content into their prompt,
    even indirectly."""
    pool = await get_pool()
    from app.auth.service import create_user

    owner_id = await create_user(pool, "mentor-lesson-owner@example.com", "testpassword123", "Test")
    attacker_id = await create_user(pool, "mentor-lesson-attacker@example.com", "testpassword123", "Test")
    path_id = await create_path(pool, owner_id, "Owner's private path", ai_available=False)
    lesson_id = await pool.fetchval(
        """SELECT l.lesson_id FROM learning_lessons l
           JOIN learning_units u ON u.unit_id = l.unit_id
           JOIN learning_modules m ON m.module_id = u.module_id
           WHERE m.path_id = $1 LIMIT 1""",
        path_id,
    )

    context = await service.build_context(pool, attacker_id, "lesson", lesson_id)
    assert context["type"] == "general"


async def test_lesson_context_includes_the_lesson_details_for_its_owner():
    pool = await get_pool()
    from app.auth.service import create_user

    user_id = await create_user(pool, "mentor-lesson-real@example.com", "testpassword123", "Test")
    path_id = await create_path(pool, user_id, "Learn origami", ai_available=False)
    lesson_id = await pool.fetchval(
        """SELECT l.lesson_id FROM learning_lessons l
           JOIN learning_units u ON u.unit_id = l.unit_id
           JOIN learning_modules m ON m.module_id = u.module_id
           WHERE m.path_id = $1 ORDER BY l.position LIMIT 1""",
        path_id,
    )

    context = await service.build_context(pool, user_id, "lesson", lesson_id)
    assert context["type"] == "lesson"
    assert context["lesson_title"] == "Get oriented with the topic"
    assert context["path_title"] == "Learn origami"


async def test_ai_reply_uses_the_lesson_context_in_the_prompt(client, monkeypatch):
    captured_prompts = []

    async def fake_generate(prompt: str, temperature: float = 0.0, **_: object) -> str:
        captured_prompts.append(prompt)
        return "This is a simple explanation."

    monkeypatch.setattr(service, "generate", fake_generate)
    monkeypatch.setattr("app.mentor.router.llm_is_configured", lambda: True)

    await signup(client, "mentor-ai@example.com")
    pool = await get_pool()
    user_id = await pool.fetchval("SELECT user_id FROM users WHERE email = $1", "mentor-ai@example.com")
    path_id = await create_path(pool, user_id, "Learn origami", ai_available=False)
    lesson_id = await pool.fetchval(
        """SELECT l.lesson_id FROM learning_lessons l
           JOIN learning_units u ON u.unit_id = l.unit_id
           JOIN learning_modules m ON m.module_id = u.module_id
           WHERE m.path_id = $1 ORDER BY l.position LIMIT 1""",
        path_id,
    )

    response = await client.post(
        "/mentor/message",
        data={"text": "Can you explain this?", "context_type": "lesson", "context_id": str(lesson_id)},
    )
    assert response.status_code == 200
    assert "This is a simple explanation." in response.text
    assert len(captured_prompts) == 1
    assert "Get oriented with the topic" in captured_prompts[0]  # the lesson title reached the prompt
    assert "AI-generated" in response.text  # a real LLM reply does get the disclaimer


async def test_exhausted_budget_gets_a_friendly_message_not_a_500(client, monkeypatch):
    monkeypatch.setattr(settings, "llm_daily_budget", 0)
    monkeypatch.setattr("app.mentor.router.llm_is_configured", lambda: True)

    await signup(client, "mentor-budget@example.com")
    response = await client.post("/mentor/message", data={"text": "Hi", "context_type": "general"})
    assert response.status_code == 200
    assert "used all your AI generations for today" in response.text


async def test_new_chat_is_a_no_op_when_the_current_chat_is_empty():
    pool = await get_pool()
    from app.auth.service import create_user

    user_id = await create_user(pool, "mentor-newchat-empty@example.com", "testpassword123", "Test")
    first = await service.get_or_create_conversation(pool, user_id, "general", None)
    second = await service.start_new_chat(pool, user_id, "general", None)
    assert first == second


async def test_new_chat_ends_the_old_session_and_starts_a_fresh_one():
    pool = await get_pool()
    from app.auth.service import create_user

    user_id = await create_user(pool, "mentor-newchat@example.com", "testpassword123", "Test")
    old_convo = await service.get_or_create_conversation(pool, user_id, "general", None)
    await service.send_message(pool, user_id, old_convo, "First chat message", context={}, ai_available=False)

    new_convo = await service.start_new_chat(pool, user_id, "general", None)
    assert new_convo != old_convo

    # The old session is ended, not deleted -- its messages are untouched.
    old_messages = await service.list_messages(pool, user_id, old_convo)
    assert len(old_messages) == 2  # the user's message + the canned reply
    new_messages = await service.list_messages(pool, user_id, new_convo)
    assert new_messages == []

    # Reopening the panel now lands on the new, empty session.
    assert await service.get_or_create_conversation(pool, user_id, "general", None) == new_convo


async def test_clear_conversation_deletes_messages_but_keeps_the_conversation():
    pool = await get_pool()
    from app.auth.service import create_user

    user_id = await create_user(pool, "mentor-clear@example.com", "testpassword123", "Test")
    convo = await service.get_or_create_conversation(pool, user_id, "general", None)
    await service.send_message(pool, user_id, convo, "Hello", context={}, ai_available=False)
    assert len(await service.list_messages(pool, user_id, convo)) == 2

    await service.clear_conversation(pool, user_id, convo)
    assert await service.list_messages(pool, user_id, convo) == []
    # Same conversation_id, not a new one -- reopening still lands here.
    assert await service.get_or_create_conversation(pool, user_id, "general", None) == convo


async def test_clearing_another_users_conversation_is_rejected():
    pool = await get_pool()
    from app.auth.service import create_user

    owner_id = await create_user(pool, "mentor-clear-owner@example.com", "testpassword123", "Test")
    attacker_id = await create_user(pool, "mentor-clear-attacker@example.com", "testpassword123", "Test")
    convo = await service.get_or_create_conversation(pool, owner_id, "general", None)

    with pytest.raises(service.ConversationNotFound):
        await service.clear_conversation(pool, attacker_id, convo)


async def test_list_sessions_only_shows_ended_sessions_with_messages():
    pool = await get_pool()
    from app.auth.service import create_user

    user_id = await create_user(pool, "mentor-sessions@example.com", "testpassword123", "Test")
    first = await service.get_or_create_conversation(pool, user_id, "general", None)
    await service.send_message(pool, user_id, first, "Session one", context={}, ai_available=False)
    second = await service.start_new_chat(pool, user_id, "general", None)
    await service.send_message(pool, user_id, second, "Session two", context={}, ai_available=False)
    await service.start_new_chat(pool, user_id, "general", None)  # ends `second`, active one stays empty

    sessions = await service.list_sessions(pool, user_id, "general", None)
    # Only the two ended sessions that actually have messages -- the
    # brand-new empty active session isn't "ended" and doesn't show up.
    assert [s["conversation_id"] for s in sessions] == [second, first]
    assert sessions[0]["message_count"] == 2  # user message + canned reply
    assert sessions[0]["preview"] == "Session two"


async def test_switch_to_session_resumes_an_old_chat():
    pool = await get_pool()
    from app.auth.service import create_user

    user_id = await create_user(pool, "mentor-resume@example.com", "testpassword123", "Test")
    old_convo = await service.get_or_create_conversation(pool, user_id, "general", None)
    await service.send_message(pool, user_id, old_convo, "Old chat", context={}, ai_available=False)
    new_convo = await service.start_new_chat(pool, user_id, "general", None)
    await service.send_message(pool, user_id, new_convo, "New chat", context={}, ai_available=False)

    resumed = await service.switch_to_session(pool, user_id, old_convo)
    assert resumed == old_convo
    # The old session is active again, and the newer one is now the one
    # that shows up in history instead.
    assert await service.get_or_create_conversation(pool, user_id, "general", None) == old_convo
    sessions = await service.list_sessions(pool, user_id, "general", None)
    assert [s["conversation_id"] for s in sessions] == [new_convo]


async def test_resuming_another_users_session_is_rejected():
    pool = await get_pool()
    from app.auth.service import create_user

    owner_id = await create_user(pool, "mentor-resume-owner@example.com", "testpassword123", "Test")
    attacker_id = await create_user(pool, "mentor-resume-attacker@example.com", "testpassword123", "Test")
    convo = await service.get_or_create_conversation(pool, owner_id, "general", None)

    with pytest.raises(service.ConversationNotFound):
        await service.switch_to_session(pool, attacker_id, convo)


async def test_conversation_panel_shows_new_chat_clear_and_history_controls(client):
    await signup(client, "mentor-controls@example.com")
    response = await client.get("/mentor/conversation")
    assert response.status_code == 200
    assert "+ New chat" in response.text
    assert ">Clear<" in response.text
    assert ">History<" in response.text


async def test_new_chat_route_starts_a_fresh_session(client):
    await signup(client, "mentor-newchat-route@example.com")
    await client.post("/mentor/message", data={"text": "Hello", "context_type": "general"})
    response = await client.post("/mentor/new-chat", data={"context_type": "general"})
    assert response.status_code == 200
    assert "Hello" not in response.text  # the new session starts empty


async def test_clear_route_wipes_the_current_chat(client):
    await signup(client, "mentor-clear-route@example.com")
    await client.post("/mentor/message", data={"text": "Please clear me", "context_type": "general"})
    response = await client.post("/mentor/clear", data={"context_type": "general"})
    assert response.status_code == 200
    assert "Please clear me" not in response.text


async def test_history_route_lists_past_sessions_and_resume_reopens_one(client):
    await signup(client, "mentor-history-route@example.com")
    await client.post("/mentor/message", data={"text": "Old session text", "context_type": "general"})
    await client.post("/mentor/new-chat", data={"context_type": "general"})

    history_page = await client.get("/mentor/history?context_type=general")
    assert history_page.status_code == 200
    assert "1 message" in history_page.text or "message" in history_page.text
    assert "Back to chat" in history_page.text

    pool = await get_pool()
    user_id = await pool.fetchval(
        "SELECT user_id FROM users WHERE email = $1", "mentor-history-route@example.com"
    )
    old_convo = await pool.fetchval(
        "SELECT conversation_id FROM mentor_conversations WHERE user_id = $1 AND ended_at IS NOT NULL",
        user_id,
    )
    resumed = await client.post(f"/mentor/history/{old_convo}/resume", data={"context_type": "general"})
    assert resumed.status_code == 200
    assert "Old session text" in resumed.text


async def test_resuming_a_nonexistent_session_404s(client):
    await signup(client, "mentor-history-404@example.com")
    response = await client.post("/mentor/history/999999999/resume", data={"context_type": "general"})
    assert response.status_code == 404


async def test_another_users_conversation_history_never_leaks(client):
    """Two students asking the mentor about "general" topics must never
    see each other's messages -- get_or_create_conversation always scopes
    to the requesting user_id, so this is really a get_or_create/list
    isolation check."""
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    transport = ASGITransport(app=app)
    other = AsyncClient(transport=transport, base_url="http://test")
    await signup(other, "mentor-other@example.com")
    await other.post("/mentor/message", data={"text": "This is a secret question", "context_type": "general"})

    await signup(client, "mentor-viewer@example.com")
    response = await client.get("/mentor/conversation")
    assert "This is a secret question" not in response.text

    await other.aclose()
