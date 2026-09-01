from datetime import date, timedelta

from app.core.db import get_pool
from app.practice.service import create_question, record_attempt
from tests.conftest import signup


async def test_topbar_has_search_theme_mentor_and_profile_controls(client):
    await signup(client, "shell-topbar@example.com")
    response = await client.get("/dashboard")
    assert response.status_code == 200
    assert 'action="/practice/search"' in response.text
    assert 'id="theme-toggle"' in response.text
    assert 'id="mentor-toggle"' in response.text
    assert 'id="a11y-menu-trigger"' in response.text
    assert 'id="profile-menu-trigger"' in response.text


async def test_mentor_panel_is_present_with_its_own_landmark(client):
    """The panel body itself is real chat now (Phase 11) -- HTMX-loaded
    from /mentor/conversation on page load, not server-rendered text
    here, so this just checks the landmark and load wiring are present."""
    await signup(client, "shell-mentor@example.com")
    response = await client.get("/dashboard")
    assert '<aside class="mentor-panel" id="mentor-panel" aria-label="Loop Mentor">' in response.text
    assert 'hx-get="/mentor/conversation?context_type=general' in response.text


async def test_a11y_menu_has_all_three_toggles(client):
    await signup(client, "shell-a11y@example.com")
    response = await client.get("/dashboard")
    assert 'data-a11y="large"' in response.text
    assert 'data-a11y="dyslexia"' in response.text
    assert 'data-a11y="motion"' in response.text


async def test_streak_badge_hidden_for_a_fresh_user_with_no_attempts(client):
    await signup(client, "shell-streak-zero@example.com")
    response = await client.get("/dashboard")
    assert "topbar-streak" not in response.text


async def test_streak_badge_shows_once_a_streak_exists(client):
    await signup(client, "shell-streak@example.com")
    pool = await get_pool()

    # Two attempts on two consecutive days (today and yesterday) is a
    # streak of 2 per streak_days()'s own definition.
    row = await pool.fetchrow("SELECT user_id FROM users WHERE email = $1", "shell-streak@example.com")
    user_id = row["user_id"]
    q1 = await create_question(pool, user_id, {"question": "Q1", "answer": "A", "topic": "t"})
    q2 = await create_question(pool, user_id, {"question": "Q2", "answer": "A", "topic": "t"})
    await record_attempt(pool, user_id, q1, rating=3)
    await record_attempt(pool, user_id, q2, rating=3)
    await pool.execute(
        "UPDATE attempts SET practiced_at = $2 WHERE question_id = $1", q2, date.today() - timedelta(days=1)
    )

    response = await client.get("/dashboard")
    assert '<span class="topbar-streak" title="Daily practice streak">' in response.text
    assert "2" in response.text
