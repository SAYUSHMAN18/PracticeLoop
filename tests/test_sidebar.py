from tests.conftest import signup


async def test_sidebar_shows_avatar_initial_name_and_email(client):
    await signup(client, "sidebar@example.com", name="Sidebar Person")
    response = await client.get("/dashboard")
    assert response.status_code == 200
    assert '<div class="sidebar-avatar" aria-hidden="true">S</div>' in response.text
    assert "Sidebar Person" in response.text
    assert "sidebar@example.com" in response.text


async def test_sidebar_has_every_main_nav_item_and_mobile_toggle(client):
    await signup(client, "sidebar-nav@example.com")
    response = await client.get("/dashboard")
    for href in ("/dashboard", "/practice", "/practice/review", "/jobs", "/documents", "/profile"):
        assert f'href="{href}"' in response.text
    assert 'action="/logout"' in response.text
    assert 'id="sidebar-toggle"' in response.text
    assert 'id="sidebar-backdrop"' in response.text


async def test_sidebar_active_link_matches_current_page(client):
    await signup(client, "sidebar-active@example.com")

    dashboard = await client.get("/dashboard")
    assert '<a href="/dashboard" class="active">Dashboard</a>' in dashboard.text
    assert '<a href="/documents" class="">Documents</a>' in dashboard.text

    documents = await client.get("/documents")
    assert '<a href="/documents" class="active">Documents</a>' in documents.text
    assert '<a href="/dashboard" class="">Dashboard</a>' in documents.text


async def test_sidebar_jobs_link_stays_active_across_every_jobs_subpage(client):
    await signup(client, "sidebar-jobs@example.com")
    for path in ("/jobs", "/jobs/applications", "/jobs/gap-analysis", "/jobs/trends", "/jobs/runs"):
        response = await client.get(path)
        assert '<a href="/jobs" class="active">Jobs</a>' in response.text, path
