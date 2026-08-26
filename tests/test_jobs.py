from __future__ import annotations

from datetime import date

from app.auth.service import get_user_by_email
from app.core.config import settings
from app.core.db import get_pool
from app.jobs import applications, service
from app.jobs.scoring import keyword_fit_score
from app.jobs.sources import RawListing
from tests.conftest import signup


def _fake_listing(external_id: str = "1", title: str = "Backend Engineer") -> RawListing:
    return RawListing(
        source="fake",
        external_id=external_id,
        title=title,
        company="Acme",
        location="Remote",
        description="Python FastAPI Postgres asyncpg pgvector backend engineer role",
        url="https://example.com/job/1",
    )


def test_keyword_fit_score_scores_against_listing_vocabulary():
    listing = _fake_listing()
    strong_match = keyword_fit_score(listing, "Experienced with Python FastAPI Postgres asyncpg pgvector")
    no_match = keyword_fit_score(listing, "Watercolor painting and ceramics")
    assert strong_match > no_match
    assert strong_match > 50
    assert no_match == 0


def test_keyword_fit_score_handles_empty_listing_text():
    empty_listing = RawListing(
        source="fake", external_id="2", title="", company="", location="", description="", url=""
    )
    assert keyword_fit_score(empty_listing, "anything") == 0


async def test_discover_for_user_dedupes_and_scores(monkeypatch):
    async def fake_fetch(keywords: str, max_results: int = 25) -> list[RawListing]:
        return [_fake_listing("dup-1"), _fake_listing("dup-1"), _fake_listing("dup-2", "Data Engineer")]

    monkeypatch.setattr(service, "SOURCES", (fake_fetch,))

    pool = await get_pool()
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    await signup(client, "discover@example.com")
    user = await get_user_by_email(pool, "discover@example.com")

    stored = await service.discover_for_user(
        pool, user["user_id"], "backend engineer", "Python FastAPI Postgres backend engineer"
    )
    # Two distinct external_ids in the batch; the repeated "dup-1" must not
    # be stored twice even within a single run, let alone across reruns.
    assert stored == 2

    listings = await service.list_listings(pool, user["user_id"])
    assert len(listings) == 2
    assert all(item["fit_method"] == "keyword" for item in listings)
    assert all(item["fit_score"] is not None for item in listings)

    # Rerunning with the same fetch results must insert nothing new.
    restored = await service.discover_for_user(
        pool, user["user_id"], "backend engineer", "Python FastAPI Postgres backend engineer"
    )
    assert restored == 0

    await client.aclose()


async def test_run_discovery_records_a_job_run_even_with_no_candidates():
    run_id = await service.run_discovery(await get_pool())
    run = await service.get_run(await get_pool(), run_id)
    assert run["status"] == "success"
    assert run["users_processed"] == 0


async def test_run_discovery_survives_one_users_source_blowing_up(monkeypatch, client):
    """Partial failure is survivable: one user's discovery erroring must
    not stop the run or corrupt the persisted outcome."""

    async def exploding_fetch(keywords: str, max_results: int = 25):
        raise RuntimeError("source is down")

    monkeypatch.setattr(service, "SOURCES", (exploding_fetch,))

    await signup(client, "explode@example.com")
    pool = await get_pool()
    await pool.execute(
        "UPDATE profiles SET target_role = 'backend engineer' WHERE user_id = "
        "(SELECT user_id FROM users WHERE email = 'explode@example.com')"
    )

    run_id = await service.run_discovery(pool)
    run = await service.get_run(pool, run_id)
    # The source raised, but discover_for_user catches per-source errors
    # internally, so the user itself still counts as processed.
    assert run["status"] == "success"
    assert run["users_processed"] == 1
    assert run["listings_found"] == 0


async def test_cron_endpoint_requires_correct_token(client, monkeypatch):
    monkeypatch.setattr(settings, "jobs_cron_token", "correct-token")

    no_token = await client.post("/jobs/cron/discover")
    assert no_token.status_code == 401

    wrong_token = await client.post("/jobs/cron/discover", headers={"Authorization": "Bearer wrong"})
    assert wrong_token.status_code == 401

    right_token = await client.post("/jobs/cron/discover", headers={"Authorization": "Bearer correct-token"})
    assert right_token.status_code == 200


async def test_cron_endpoint_fails_closed_when_unconfigured(client, monkeypatch):
    """An empty configured token must never be satisfiable by an empty
    submitted one -- 'unconfigured' must not be a backdoor 'disabled'."""
    monkeypatch.setattr(settings, "jobs_cron_token", "")
    response = await client.post("/jobs/cron/discover", headers={"Authorization": "Bearer "})
    assert response.status_code == 503


async def test_application_tracker_create_update_and_funnel(client):
    await signup(client, "tracker@example.com")

    created = await client.post("/jobs/applications", data={"company": "Acme", "role": "Backend Engineer"})
    assert created.status_code == 303

    pool = await get_pool()
    user = await get_user_by_email(pool, "tracker@example.com")
    apps = await applications.list_applications(pool, user["user_id"])
    assert len(apps) == 1
    application_id = apps[0]["application_id"]

    updated = await client.post(
        f"/jobs/applications/{application_id}/status", data={"status": "interviewing"}
    )
    assert updated.status_code == 303

    stats = await applications.funnel_stats(pool, user["user_id"])
    assert stats["total"] == 1
    assert stats["interview_rate"] == 100


async def test_updating_another_users_application_is_404(client):
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    victim = client
    attacker = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    await signup(victim, "app-victim@example.com")
    await signup(attacker, "app-attacker@example.com")

    await victim.post("/jobs/applications", data={"company": "Acme", "role": "Engineer"})
    pool = await get_pool()
    user = await get_user_by_email(pool, "app-victim@example.com")
    apps = await applications.list_applications(pool, user["user_id"])
    application_id = apps[0]["application_id"]

    response = await attacker.post(f"/jobs/applications/{application_id}/status", data={"status": "rejected"})
    assert response.status_code == 404

    await attacker.aclose()


async def test_due_follow_ups_and_stale_detection(client):
    await signup(client, "followup@example.com")
    pool = await get_pool()
    user = await get_user_by_email(pool, "followup@example.com")

    await applications.create_application(
        pool, user["user_id"], "Acme", "Engineer", follow_up_at=date.today()
    )
    due = await applications.due_follow_ups(pool, user["user_id"])
    assert len(due) == 1

    stale = await applications.stale_applications(pool, user["user_id"], today=date.today())
    assert stale == []  # applied today, not stale yet
