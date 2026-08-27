import re

from tests.conftest import signup


async def test_stylesheet_link_is_cache_busted_with_a_content_hash(client):
    """Regression test: base.html/base_auth.html used to link a bare
    /static/style.css with no versioning. A deploy that changed the CSS
    but not that URL let browsers keep serving an old cached copy against
    new HTML -- exactly what happened when the sidebar shipped: the new
    markup had no matching rules under the stale stylesheet. The link
    must now carry a ?v=<hash> that changes whenever style.css's content
    does, on both the authenticated shell and the logged-out auth pages."""
    login_page = await client.get("/login")
    match = re.search(r'href="/static/style\.css\?v=([0-9a-f]+)"', login_page.text)
    assert match, "expected a version-hashed stylesheet link on the login page"

    await signup(client, "static-asset@example.com")
    dashboard = await client.get("/dashboard")
    dashboard_match = re.search(r'href="/static/style\.css\?v=([0-9a-f]+)"', dashboard.text)
    assert dashboard_match, "expected a version-hashed stylesheet link on an authenticated page"

    # Same running app, same file -- both pages must reference the same version.
    assert match.group(1) == dashboard_match.group(1)


async def test_versioned_stylesheet_url_serves_the_real_file_and_is_cached_forever(client):
    login_page = await client.get("/login")
    match = re.search(r'href="(/static/style\.css\?v=[0-9a-f]+)"', login_page.text)
    assert match

    response = await client.get(match.group(1))
    assert response.status_code == 200
    assert "sidebar" in response.text  # the actual, current stylesheet -- not an empty/placeholder response
    assert response.headers["cache-control"] == "public, max-age=31536000, immutable"
