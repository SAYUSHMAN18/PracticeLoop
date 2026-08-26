from tests.conftest import signup


async def test_signup_then_dashboard_shows_zero_state(client):
    await signup(client, "student@example.com")
    response = await client.get("/dashboard")
    assert response.status_code == 200
    assert "day streak" in response.text


async def test_duplicate_signup_is_rejected(client):
    await signup(client, "dup@example.com")
    response = await client.post(
        "/signup",
        data={"name": "Again", "email": "dup@example.com", "password": "testpassword123"},
    )
    assert response.status_code == 400
    assert "already registered" in response.text


async def test_wrong_password_is_rejected(client):
    await signup(client, "wrongpw@example.com")
    response = await client.post(
        "/login", data={"email": "wrongpw@example.com", "password": "not-the-password"}
    )
    assert response.status_code == 400


async def test_anonymous_access_redirects_to_login():
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test", follow_redirects=False) as ac:
        for path in ("/dashboard", "/practice", "/practice/review", "/profile"):
            response = await ac.get(path)
            assert response.status_code == 303, path
            assert response.headers["location"] == "/login", path


async def test_password_over_72_bytes_gives_friendly_error_not_500(client):
    response = await client.post(
        "/signup",
        data={"name": "Long", "email": "long@example.com", "password": "x" * 100},
    )
    assert response.status_code == 400
    assert "72 bytes" in response.text


async def test_login_with_nonexistent_email_is_a_clean_400_not_a_crash(client):
    """Regression guard for the authenticate() timing fix: a nonexistent
    email must still hit the same code path (dummy bcrypt compare) as a
    wrong password, not short-circuit into a different error or a 500."""
    response = await client.post(
        "/login", data={"email": "nobody@example.com", "password": "whatever123"}
    )
    assert response.status_code == 400
    assert "Incorrect email or password" in response.text


async def test_responses_carry_baseline_security_headers(client):
    await signup(client, "headers@example.com")
    response = await client.get("/dashboard")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "strict-origin-when-cross-origin"
