from __future__ import annotations

from app.auth.service import get_user_by_email
from app.core.db import get_pool
from app.jobs import market_trends
from tests.conftest import signup


async def _insert_listing(pool, user_id: int, external_id: str, title: str, description: str = ""):
    await pool.execute(
        """INSERT INTO job_listings (user_id, source, external_id, title, description)
           VALUES ($1, 'fake', $2, $3, $4)""",
        user_id,
        external_id,
        title,
        description,
    )


async def test_compute_skill_demand_counts_across_all_users(client):
    await signup(client, "trend-a@example.com")
    pool = await get_pool()
    user_a = await get_user_by_email(pool, "trend-a@example.com")

    from httpx import ASGITransport, AsyncClient

    from app.main import app

    client_b = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    await signup(client_b, "trend-b@example.com")
    user_b = await get_user_by_email(pool, "trend-b@example.com")

    await _insert_listing(pool, user_a["user_id"], "1", "Backend Engineer (Python, Docker)")
    await _insert_listing(pool, user_b["user_id"], "1", "Backend Engineer (Python, Kubernetes)")

    trends = await market_trends.compute_skill_demand(pool)
    counts = dict(trends["top_skills"])
    # Aggregated across both users, not scoped to whoever called it --
    # this is the one deliberately cross-user view in the app.
    assert counts["Python"] == 2
    assert counts.get("Docker", 0) == 1
    assert counts.get("Kubernetes", 0) == 1
    assert trends["total_listings"] == 2

    await client_b.aclose()


async def test_small_sample_is_flagged():
    pool = await get_pool()
    trends = await market_trends.compute_skill_demand(pool)
    assert trends["total_listings"] < 40
    assert trends["small_sample"] is True


async def test_trends_page_renders_for_a_logged_in_user(client):
    await signup(client, "trends-page@example.com")
    response = await client.get("/jobs/trends")
    assert response.status_code == 200
    assert "Market trends" in response.text


def test_tag_skills_finds_known_skills_case_insensitively():
    tags = market_trends.tag_skills("Looking for a python developer with AWS and react experience")
    assert set(tags) == {"Python", "AWS", "React"}


def test_tag_skills_returns_nothing_for_unrelated_text():
    assert market_trends.tag_skills("Watercolor painting and ceramics") == []
