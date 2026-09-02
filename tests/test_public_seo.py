"""The public, crawlable surface.

Before the landing page existed, "/" redirected straight to "/login" -- a
crawler or an AI assistant following a link found a login form and nothing
else. These tests pin the two things that make the page worth having: real
content at a stable URL, and structured data that agrees with it.
"""

from __future__ import annotations

import json
import re

from app.public import content
from tests.conftest import signup


def _json_ld(html: str) -> dict:
    match = re.search(r'<script type="application/ld\+json"[^>]*>(.*?)</script>', html, re.S)
    assert match, "no JSON-LD block on the landing page"
    return json.loads(match.group(1))


async def test_the_root_url_serves_a_real_page_to_a_visitor(client):
    response = await client.get("/")
    assert response.status_code == 200
    assert "Turn a goal into a study plan" in response.text
    # the content, not just a shell
    assert "How it works" in response.text
    assert "Frequently asked questions" in response.text


async def test_a_logged_in_user_still_gets_their_dashboard(client):
    """Someone who already uses the product doesn't need the pitch for it."""
    await signup(client, "landing-member@example.com")
    response = await client.get("/", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == "/dashboard"


async def test_the_landing_page_carries_a_full_meta_set(client):
    html = (await client.get("/")).text
    assert "<title>PracticeLoop" in html
    assert f'content="{content.META_DESCRIPTION}"' in html
    assert '<link rel="canonical"' in html
    assert 'property="og:title"' in html
    assert 'property="og:description"' in html
    assert 'name="twitter:card"' in html
    assert 'name="robots" content="index, follow' in html


async def test_exactly_one_h1_and_a_real_heading_hierarchy(client):
    html = (await client.get("/")).text
    assert len(re.findall(r"<h1[\s>]", html)) == 1
    assert len(re.findall(r"<h2[\s>]", html)) >= 4


async def test_structured_data_is_valid_and_matches_the_visible_faq(client):
    """Structured data that contradicts the page is worse than none -- it's
    a manual-action risk, and a generative engine will quote whichever it
    saw last."""
    html = (await client.get("/")).text
    data = _json_ld(html)

    assert data["@context"] == "https://schema.org"
    types = {node["@type"] for node in data["@graph"]}
    assert types == {"SoftwareApplication", "FAQPage"}

    faq_node = next(n for n in data["@graph"] if n["@type"] == "FAQPage")
    assert len(faq_node["mainEntity"]) == len(content.FAQ)
    for question in faq_node["mainEntity"]:
        assert question["name"] in html, f"{question['name']!r} is in JSON-LD but not on the page"


async def test_json_ld_cannot_break_out_of_its_own_script_tag(client):
    html = (await client.get("/")).text
    match = re.search(r'<script type="application/ld\+json"[^>]*>(.*?)</script>', html, re.S)
    assert "<" not in match.group(1), "a raw < in JSON-LD could close the script tag early"


async def test_app_pages_are_noindex(client):
    """Everything behind the login redirects to /login for a crawler
    anyway; saying noindex keeps crawl budget on the page that has content."""
    await signup(client, "noindex@example.com")
    html = (await client.get("/dashboard")).text
    assert 'name="robots" content="noindex, nofollow"' in html


async def test_robots_txt_points_at_the_sitemap_and_blocks_the_app(client):
    response = await client.get("/robots.txt")
    assert response.status_code == 200
    body = response.text
    assert "User-agent: *" in body
    assert "Sitemap: " in body and "/sitemap.xml" in body
    assert "Disallow: /dashboard" in body
    assert "Allow: /signup" in body


async def test_sitemap_is_valid_xml_listing_the_public_urls(client):
    response = await client.get("/sitemap.xml")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/xml")
    body = response.text
    assert body.startswith("<?xml")
    assert "<urlset" in body
    assert body.count("<url>") == 3
    assert "/signup</loc>" in body


async def test_llms_txt_describes_the_product_and_its_limits(client):
    """The /llms.txt convention: what an AI assistant reads instead of
    crawling. It has to carry the mechanism and the honest caveats, or an
    assistant summarising this app will just make something up."""
    response = await client.get("/llms.txt")
    assert response.status_code == 200
    body = response.text
    assert body.startswith("# PracticeLoop")
    assert "FSRS" in body
    assert "Honest limitations" in body
    assert "never" in body  # the "never fabricates a result" guarantee
    for item in content.FAQ:
        assert item["q"] in body


async def test_login_and_signup_stay_indexable_with_a_canonical(client):
    for path in ("/login", "/signup"):
        html = (await client.get(path)).text
        assert '<link rel="canonical"' in html, path
        assert 'name="description"' in html, path
        assert "noindex" not in html, path
