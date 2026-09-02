import json

from app.core.db import get_pool
from tests.conftest import signup


async def test_export_returns_a_well_formed_json_file_with_real_data(client):
    await signup(client, "acct-export@example.com", name="Exporter")
    await client.post(
        "/profile",
        data={"target_role": "Backend Engineer", "target_companies": "Acme"},
    )

    response = await client.get("/account/export")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert "attachment" in response.headers["content-disposition"]

    data = json.loads(response.text)
    assert data["account"]["email"] == "acct-export@example.com"
    assert data["account"]["name"] == "Exporter"
    assert data["profile"]["target_role"] == "Backend Engineer"
    assert "questions" in data
    assert "learning_paths" in data
    assert "xp_summary" in data


async def test_export_requires_login(client):
    response = await client.get("/account/export", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


async def test_delete_with_wrong_password_does_not_delete_the_account(client):
    await signup(client, "acct-wrongpw@example.com")

    response = await client.post("/account/delete", data={"password": "not-the-real-password"})
    assert response.status_code == 400
    assert "your account was not deleted" in response.text

    pool = await get_pool()
    still_there = await pool.fetchval(
        "SELECT user_id FROM users WHERE email = $1", "acct-wrongpw@example.com"
    )
    assert still_there is not None

    # The session is still valid -- a failed delete attempt doesn't log
    # the user out.
    dashboard = await client.get("/dashboard", follow_redirects=False)
    assert dashboard.status_code == 200


async def test_delete_with_correct_password_deletes_the_account_and_logs_out(client):
    await signup(client, "acct-delete@example.com", password="testpassword123")
    pool = await get_pool()
    user_id = await pool.fetchval("SELECT user_id FROM users WHERE email = $1", "acct-delete@example.com")

    document_id = await pool.fetchval(
        """INSERT INTO documents
               (user_id, doc_type, title, filename, mime_type, size_bytes, content_bytes, extracted_text)
           VALUES ($1, 'other', 'Note', 'note.txt', 'text/plain', 5, 'hello', '')
           RETURNING document_id""",
        user_id,
    )

    response = await client.post(
        "/account/delete", data={"password": "testpassword123"}, follow_redirects=False
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/login?deleted=1"

    assert await pool.fetchval("SELECT user_id FROM users WHERE user_id = $1", user_id) is None
    remaining_doc = await pool.fetchval(
        "SELECT document_id FROM documents WHERE document_id = $1", document_id
    )
    assert remaining_doc is None

    # The session was cleared -- a protected route now redirects to login.
    dashboard = await client.get("/dashboard", follow_redirects=False)
    assert dashboard.status_code == 303
    assert dashboard.headers["location"] == "/login"


async def test_login_page_shows_the_deleted_account_notice(client):
    response = await client.get("/login?deleted=1")
    assert response.status_code == 200
    assert "have been deleted" in response.text

    response = await client.get("/login")
    assert "have been deleted" not in response.text
