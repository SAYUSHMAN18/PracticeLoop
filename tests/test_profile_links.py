"""Portfolio links -- GitHub / LinkedIn / website on the profile, a code
repo on a project, both surfaced on the portfolio."""

from __future__ import annotations

import pytest

from app.core.db import get_pool
from app.core.links import clean_url
from tests.conftest import signup

_PROFILE_FORM = {
    "target_role": "",
    "target_companies": "",
    "goal_type": "",
    "target_date": "",
    "daily_time_budget_minutes": "",
    "timezone": "",
    "day_rollover_hour": "0",
    "proficiency_level": "",
}


# ---------- the URL cleaner ----------


@pytest.mark.parametrize(
    "raw,host,expected",
    [
        ("github.com/octocat", "github.com", "https://github.com/octocat"),
        ("https://www.linkedin.com/in/x/", "linkedin.com", "https://www.linkedin.com/in/x"),
        ("http://phish.example/gh", "github.com", ""),  # wrong host
        ("javascript:alert(1)", "", ""),
        ("not a url", "", ""),
        ("ftp://x.com/a", "", ""),
        ("https://my-site.dev", "", "https://my-site.dev"),
    ],
)
def test_clean_url(raw, host, expected):
    assert clean_url(raw, host_contains=host) == expected


# ---------- profile form ----------


async def test_profile_links_save_and_render_on_the_portfolio(client):
    await signup(client, "links@example.com")
    r = await client.post(
        "/profile",
        data={
            **_PROFILE_FORM,
            "github_url": "github.com/me",
            "linkedin_url": "https://linkedin.com/in/me",
            "website_url": "me.dev",
        },
    )
    assert r.status_code in (200, 303)

    portfolio = await client.get("/portfolio")
    assert 'href="https://github.com/me"' in portfolio.text
    assert 'href="https://linkedin.com/in/me"' in portfolio.text
    assert 'href="https://me.dev"' in portfolio.text


async def test_a_bad_link_is_dropped_not_rejected(client):
    await signup(client, "badlink@example.com")
    r = await client.post(
        "/profile",
        data={**_PROFILE_FORM, "github_url": "not a link", "linkedin_url": "http://evil.com/in/x"},
    )
    assert r.status_code in (200, 303)  # form still saved
    pool = await get_pool()
    row = await pool.fetchrow(
        "SELECT github_url, linkedin_url FROM profiles p JOIN users u USING (user_id) WHERE u.email = $1",
        "badlink@example.com",
    )
    assert row["github_url"] == ""
    assert row["linkedin_url"] == ""  # wrong host -> dropped


async def test_portfolio_prompts_when_no_links_are_set(client):
    await signup(client, "nolinks@example.com")
    portfolio = await client.get("/portfolio")
    assert "Add your GitHub" in portfolio.text


# ---------- project repo ----------


async def test_project_repo_link_saves_and_shows_on_the_portfolio(client):
    await signup(client, "repo@example.com")
    # create a project (no AI -> fallback idea)
    await client.post("/projects", data={"topic": "a weather CLI"})
    pool = await get_pool()
    pid = await pool.fetchval(
        "SELECT project_id FROM projects p JOIN users u USING (user_id) WHERE u.email = $1",
        "repo@example.com",
    )

    r = await client.post(f"/projects/{pid}/repo", data={"repo_url": "github.com/me/weather"})
    assert r.status_code == 303
    detail = await client.get(f"/projects/{pid}")
    assert "https://github.com/me/weather" in detail.text

    # submit it so it reaches the portfolio
    await client.post(f"/projects/{pid}/submit", data={"submission_text": "built it", "submission_link": ""})
    portfolio = await client.get("/portfolio")
    assert 'href="https://github.com/me/weather"' in portfolio.text


async def test_another_users_project_repo_is_not_settable(client):
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    transport = ASGITransport(app=app)
    async with (
        AsyncClient(transport=transport, base_url="http://test", follow_redirects=False) as owner,
        AsyncClient(transport=transport, base_url="http://test", follow_redirects=False) as other,
    ):
        await signup(owner, "repo-owner@example.com")
        await signup(other, "repo-other@example.com")
        await owner.post("/projects", data={"topic": "x"})
        pool = await get_pool()
        pid = await pool.fetchval(
            "SELECT project_id FROM projects p JOIN users u USING (user_id) WHERE u.email = $1",
            "repo-owner@example.com",
        )
        r = await other.post(f"/projects/{pid}/repo", data={"repo_url": "github.com/other/x"})
        assert r.status_code == 404
