from httpx import ASGITransport, AsyncClient

from app.auth.service import get_user_by_email
from app.core.db import get_pool
from app.practice.service import create_question
from tests.conftest import signup


async def _two_logged_in_clients():
    from app.main import app

    transport = ASGITransport(app=app)
    client_a = AsyncClient(transport=transport, base_url="http://test")
    client_b = AsyncClient(transport=transport, base_url="http://test")
    await signup(client_a, "victim@example.com")
    await signup(client_b, "attacker@example.com")
    return client_a, client_b


async def test_user_cannot_rate_another_users_question():
    """Regression test for the IDOR in record_attempt: POST
    /practice/review/{question_id} used to accept any question_id and stamp
    the caller's user_id onto the attempt row, letting one account rate --
    and read the review history of -- another account's question."""
    client_a, client_b = await _two_logged_in_clients()

    pool = await get_pool()
    victim = await get_user_by_email(pool, "victim@example.com")
    question_id = await create_question(
        pool, victim["user_id"], {"question": "Victim's private question", "topic": "secret"}
    )

    response = await client_b.post(f"/practice/review/{question_id}", data={"rating": 5})
    assert response.status_code == 404

    attempts = await pool.fetch("SELECT * FROM attempts WHERE question_id = $1", question_id)
    assert attempts == [], "attacker's rating should never have been recorded"

    await client_a.aclose()
    await client_b.aclose()


async def test_user_cannot_edit_or_delete_another_users_question():
    client_a, client_b = await _two_logged_in_clients()

    pool = await get_pool()
    victim = await get_user_by_email(pool, "victim@example.com")
    question_id = await create_question(
        pool, victim["user_id"], {"question": "Victim's question", "topic": ""}
    )

    edit_response = await client_b.post(
        f"/practice/{question_id}/edit",
        data={"question": "hacked", "difficulty": "medium"},
    )
    assert edit_response.status_code == 404

    delete_response = await client_b.post(f"/practice/{question_id}/delete")
    assert delete_response.status_code == 404

    still_there = await pool.fetchval(
        "SELECT question FROM questions WHERE question_id = $1", question_id
    )
    assert still_there == "Victim's question"

    await client_a.aclose()
    await client_b.aclose()


async def test_owner_can_rate_their_own_question(client):
    await signup(client, "owner@example.com")

    pool = await get_pool()
    owner = await get_user_by_email(pool, "owner@example.com")
    question_id = await create_question(
        pool, owner["user_id"], {"question": "My own question", "topic": ""}
    )

    response = await client.post(f"/practice/review/{question_id}", data={"rating": 4})
    assert response.status_code == 200
    assert "Saved" in response.text
