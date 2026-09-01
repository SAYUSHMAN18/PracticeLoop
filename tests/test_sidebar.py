from tests.conftest import signup


async def test_sidebar_shows_avatar_initial_name_and_email(client):
    """The avatar/name/email moved from the sidebar to the topbar's profile
    dropdown in Phase 5's shell rework -- still server-rendered into every
    page (the dropdown just starts `hidden` until clicked), so it's still
    reachable without JS in this response.text check."""
    await signup(client, "sidebar@example.com", name="Sidebar Person")
    response = await client.get("/dashboard")
    assert response.status_code == 200
    assert '<span class="sidebar-avatar" aria-hidden="true">S</span>' in response.text
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
    """Phase 5's new information architecture renamed "Dashboard" to "Home"
    and "Documents" to "Knowledge Vault" -- hrefs are unchanged, just the
    labels shown in the sidebar."""
    await signup(client, "sidebar-active@example.com")

    dashboard = await client.get("/dashboard")
    assert '<a href="/dashboard" class="active">Home</a>' in dashboard.text
    assert '<a href="/documents" class="">Knowledge Vault</a>' in dashboard.text

    documents = await client.get("/documents")
    assert '<a href="/documents" class="active">Knowledge Vault</a>' in documents.text
    assert '<a href="/dashboard" class="">Home</a>' in documents.text


async def test_sidebar_jobs_link_stays_active_across_every_jobs_subpage(client):
    """ "Jobs" was renamed "Career Lab" in Phase 5's new information
    architecture -- still the same /jobs href."""
    await signup(client, "sidebar-jobs@example.com")
    for path in ("/jobs", "/jobs/applications", "/jobs/gap-analysis", "/jobs/trends", "/jobs/runs"):
        response = await client.get(path)
        assert '<a href="/jobs" class="active">Career Lab</a>' in response.text, path


async def test_authenticated_pages_have_a_skip_link_and_theme_toggle(client):
    await signup(client, "sidebar-a11y@example.com")
    response = await client.get("/dashboard")
    assert '<a href="#main-content" class="skip-link">Skip to content</a>' in response.text
    assert 'id="main-content"' in response.text
    assert 'id="theme-toggle"' in response.text


async def test_review_card_container_is_an_aria_live_region(client):
    await signup(client, "sidebar-arialive@example.com")
    response = await client.get("/practice/review")
    assert '<div id="review-card" aria-live="polite" aria-atomic="true">' in response.text


async def test_theme_toggle_cycles_through_high_contrast(client):
    """The toggle script's cycle order must include "contrast", not just
    system/light/dark -- a regression here would silently drop the fourth
    theme from the UI while leaving the CSS for it dead code."""
    await signup(client, "sidebar-contrast@example.com")
    response = await client.get("/dashboard")
    assert '["system", "light", "dark", "contrast"]' in response.text
    assert '"High contrast"' in response.text


async def test_stylesheet_defines_a_wcag_verified_high_contrast_theme():
    """Every pair here was checked against the WCAG contrast formula
    (text-on-bg 21:1, accent-on-white/accent-text-on-accent 7.3:1+) -- this
    just guards that those exact, verified values stay in place rather than
    silently drifting to something unchecked."""
    from app.core.templates import STATIC_DIR

    css = (STATIC_DIR / "style.css").read_text(encoding="utf-8")
    assert ':root[data-theme="contrast"]' in css
    assert "--bg: #ffffff;" in css
    assert "--text: #000000;" in css
    assert "--border: #000000;" in css
