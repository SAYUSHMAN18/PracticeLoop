from datetime import date, timedelta

from httpx import ASGITransport, AsyncClient


async def _signup_without_following_redirect(client, email: str) -> str:
    response = await client.post(
        "/signup", data={"name": "Onboard Test", "email": email, "password": "testpassword123"}
    )
    assert response.status_code == 303
    return response.headers["location"]


async def test_fresh_signup_redirects_to_welcome_not_dashboard(client):
    location = await _signup_without_following_redirect(client, "onboard-signup@example.com")
    assert location == "/welcome"


async def test_welcome_page_renders_after_signup(client):
    await _signup_without_following_redirect(client, "onboard-page@example.com")
    response = await client.get("/welcome")
    assert response.status_code == 200
    assert "Welcome to PracticeLoop" in response.text


async def test_saving_welcome_form_marks_onboarded_and_redirects_to_dashboard(client):
    await _signup_without_following_redirect(client, "onboard-save@example.com")
    target = (date.today() + timedelta(days=5)).isoformat()

    saved = await client.post(
        "/welcome",
        data={
            "target_role": "Backend Engineer",
            "goal_type": "interview_prep",
            "target_date": target,
            "daily_time_budget_minutes": "20",
        },
    )
    assert saved.status_code == 303
    assert saved.headers["location"] == "/dashboard"

    profile = await client.get("/profile")
    assert '<option value="interview_prep" selected>Prepare for an interview</option>' in profile.text
    assert "Backend Engineer" in profile.text

    # Onboarding is genuinely one-time -- logging back in must not re-show it.
    await client.post("/logout")
    login = await client.post(
        "/login", data={"email": "onboard-save@example.com", "password": "testpassword123"}
    )
    assert login.headers["location"] == "/dashboard"


async def test_skipping_welcome_marks_onboarded_without_touching_profile_fields(client):
    await _signup_without_following_redirect(client, "onboard-skip@example.com")

    skipped = await client.post("/welcome/skip")
    assert skipped.status_code == 303
    assert skipped.headers["location"] == "/dashboard"

    profile = await client.get("/profile")
    assert '<option value="" selected>Not set</option>' in profile.text

    await client.post("/logout")
    login = await client.post(
        "/login", data={"email": "onboard-skip@example.com", "password": "testpassword123"}
    )
    assert login.headers["location"] == "/dashboard"


async def test_revisiting_welcome_after_onboarding_bounces_to_the_dashboard(client):
    await _signup_without_following_redirect(client, "onboard-revisit@example.com")
    await client.post("/welcome/skip")

    revisit = await client.get("/welcome", follow_redirects=False)
    assert revisit.status_code == 303
    assert revisit.headers["location"] == "/dashboard"


async def test_login_also_redirects_to_welcome_when_not_yet_onboarded():
    """Not just signup -- logging in (e.g. the user closed the tab instead
    of finishing/skipping welcome, or this is a pre-existing account from
    before onboarding_completed existed, which defaults to false) must
    redirect the same way, since the source of truth is the DB flag, not
    "did they just sign up."""
    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await client.post(
            "/signup",
            data={
                "name": "Pre Existing",
                "email": "onboard-preexisting@example.com",
                "password": "testpassword123",
            },
        )
        await client.post("/logout")

        login = await client.post(
            "/login", data={"email": "onboard-preexisting@example.com", "password": "testpassword123"}
        )
        assert login.headers["location"] == "/welcome"
