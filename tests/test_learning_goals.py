from datetime import date, timedelta

from tests.conftest import signup


async def test_learning_goal_roundtrip(client):
    await signup(client, "goal@example.com")

    target = (date.today() + timedelta(days=14)).isoformat()
    saved = await client.post(
        "/profile",
        data={
            "target_role": "",
            "target_companies": "",
            "goal_type": "exam_prep",
            "target_date": target,
            "daily_time_budget_minutes": "45",
            "timezone": "Asia/Kolkata",
        },
    )
    assert saved.status_code == 200
    assert "Saved" in saved.text
    assert '<option value="exam_prep" selected>Prepare for an exam</option>' in saved.text
    assert "Asia/Kolkata" in saved.text
    assert 'value="45"' in saved.text


async def test_unknown_goal_type_falls_back_to_not_set(client):
    await signup(client, "goal-badtype@example.com")

    response = await client.post(
        "/profile",
        data={"target_role": "", "target_companies": "", "goal_type": "not-a-real-goal"},
    )
    assert response.status_code == 200
    assert "Saved" in response.text


async def test_malformed_target_date_and_budget_are_ignored_not_500(client):
    await signup(client, "goal-malformed@example.com")

    response = await client.post(
        "/profile",
        data={
            "target_role": "",
            "target_companies": "",
            "target_date": "not-a-date",
            "daily_time_budget_minutes": "not-a-number",
        },
    )
    assert response.status_code == 200
    assert "Saved" in response.text


async def test_dashboard_shows_a_countdown_for_a_future_goal_date(client):
    await signup(client, "goal-dashboard-future@example.com")
    target = (date.today() + timedelta(days=7)).isoformat()
    await client.post(
        "/profile",
        data={
            "target_role": "",
            "target_companies": "",
            "goal_type": "interview_prep",
            "target_date": target,
        },
    )

    dashboard = await client.get("/dashboard")
    assert "7 days" in dashboard.text
    assert "prepare for an interview" in dashboard.text


async def test_dashboard_shows_overdue_goal_with_an_update_prompt(client):
    await signup(client, "goal-dashboard-past@example.com")
    target = (date.today() - timedelta(days=3)).isoformat()
    await client.post(
        "/profile",
        data={"target_role": "", "target_companies": "", "goal_type": "exam_prep", "target_date": target},
    )

    dashboard = await client.get("/dashboard")
    assert "3 days ago" in dashboard.text
    assert "Update it" in dashboard.text


async def test_dashboard_prompts_to_set_a_goal_when_none_is_set(client):
    await signup(client, "goal-dashboard-none@example.com")
    dashboard = await client.get("/dashboard")
    assert "No goal set yet" in dashboard.text
