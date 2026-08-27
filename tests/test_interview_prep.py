from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.auth.service import get_user_by_email
from app.core.db import get_pool
from app.jobs import applications, interview_prep
from app.practice.service import list_questions
from tests.conftest import signup


async def test_upcoming_interviews_excludes_past_and_unset(client):
    await signup(client, "countdown@example.com")
    pool = await get_pool()
    user = await get_user_by_email(pool, "countdown@example.com")

    future = datetime.now(timezone.utc) + timedelta(days=3)
    past = datetime.now(timezone.utc) - timedelta(days=3)
    await applications.create_application(pool, user["user_id"], "FutureCo", "Engineer")
    await applications.create_application(pool, user["user_id"], "PastCo", "Engineer")
    await applications.create_application(pool, user["user_id"], "NoDateCo", "Engineer")

    apps = await applications.list_applications(pool, user["user_id"])
    by_company = {a["company"]: a["application_id"] for a in apps}
    await pool.execute(
        "UPDATE applications SET interview_at = $2 WHERE application_id = $1",
        by_company["FutureCo"],
        future,
    )
    await pool.execute(
        "UPDATE applications SET interview_at = $2 WHERE application_id = $1",
        by_company["PastCo"],
        past,
    )

    upcoming = await interview_prep.upcoming_interviews(pool, user["user_id"])
    assert [row["company"] for row in upcoming] == ["FutureCo"]


async def test_company_deck_matches_gap_analyzed_skills_only(client):
    await signup(client, "deck-prep@example.com")
    pool = await get_pool()
    user = await get_user_by_email(pool, "deck-prep@example.com")

    listing_id = await pool.fetchval(
        """INSERT INTO job_listings (user_id, source, external_id, title)
           VALUES ($1, 'fake', 'ext-1', 'Backend Role') RETURNING listing_id""",
        user["user_id"],
    )
    await pool.execute(
        """INSERT INTO job_skill_gaps (user_id, listing_id, skill, bucket, evidence)
           VALUES ($1, $2, 'Kafka', 'missing', 'not on resume')""",
        user["user_id"],
        listing_id,
    )
    application_id = await applications.create_application(
        pool, user["user_id"], "Acme", "Backend Engineer", listing_id=listing_id
    )

    from app.practice.service import create_question

    matching_id = await create_question(
        pool, user["user_id"], {"question": "Explain Kafka partitions", "topic": "Kafka", "answer": ""}
    )
    await create_question(
        pool, user["user_id"], {"question": "What is a hash map?", "topic": "data structures", "answer": ""}
    )

    deck = await interview_prep.get_company_deck(pool, user["user_id"], application_id)
    assert [q["question_id"] for q in deck] == [matching_id]


async def test_company_deck_empty_without_a_listing(client):
    await signup(client, "no-listing@example.com")
    pool = await get_pool()
    user = await get_user_by_email(pool, "no-listing@example.com")
    application_id = await applications.create_application(pool, user["user_id"], "Acme", "Engineer")

    deck = await interview_prep.get_company_deck(pool, user["user_id"], application_id)
    assert deck == []


async def test_debrief_creates_questions_and_saves_notes(client):
    await signup(client, "debrief@example.com")
    pool = await get_pool()
    user = await get_user_by_email(pool, "debrief@example.com")
    application_id = await applications.create_application(pool, user["user_id"], "Acme", "Engineer")

    created = await interview_prep.log_debrief(
        pool,
        user["user_id"],
        application_id,
        "How would you design a rate limiter?\n\nExplain CAP theorem\n",
        "Went okay, struggled on the rate limiter question.",
    )
    assert created == 2  # blank line skipped

    questions = await list_questions(pool, user["user_id"])
    question_texts = {q["question"] for q in questions}
    assert "How would you design a rate limiter?" in question_texts
    assert "Explain CAP theorem" in question_texts
    assert all(q["source"] == "interview_debrief" for q in questions)
    assert all(q["company"] == "Acme" for q in questions)

    application = await applications.get_application(pool, user["user_id"], application_id)
    assert "struggled" in application["notes"]


async def test_debrief_endpoint_requires_ownership(client):
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    victim = client
    attacker = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    await signup(victim, "debrief-victim@example.com")
    await signup(attacker, "debrief-attacker@example.com")

    pool = await get_pool()
    victim_user = await get_user_by_email(pool, "debrief-victim@example.com")
    application_id = await applications.create_application(pool, victim_user["user_id"], "Acme", "Engineer")

    response = await attacker.post(
        f"/jobs/applications/{application_id}/debrief", data={"questions_asked": "hacked"}
    )
    assert response.status_code == 404

    await attacker.aclose()


async def test_updating_status_without_touching_interview_date_does_not_500(client):
    """A blank <input type=\"datetime-local\"> submits an empty string, not
    an omitted field -- this must not choke on that."""
    await signup(client, "blank-date@example.com")
    pool = await get_pool()
    user = await get_user_by_email(pool, "blank-date@example.com")
    application_id = await applications.create_application(pool, user["user_id"], "Acme", "Engineer")

    response = await client.post(
        f"/jobs/applications/{application_id}/status",
        data={"status": "interviewing", "interview_at": ""},
    )
    assert response.status_code == 303


async def test_dashboard_shows_interview_countdown(client):
    await signup(client, "dash-countdown@example.com")
    pool = await get_pool()
    user = await get_user_by_email(pool, "dash-countdown@example.com")
    application_id = await applications.create_application(pool, user["user_id"], "Acme", "Engineer")
    future = datetime.now(timezone.utc) + timedelta(days=5)
    await pool.execute(
        "UPDATE applications SET interview_at = $2 WHERE application_id = $1", application_id, future
    )

    response = await client.get("/dashboard")
    assert "Acme" in response.text
    assert "Upcoming interviews" in response.text
