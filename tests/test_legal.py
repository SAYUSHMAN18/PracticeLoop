"""Terms and Privacy.

The app collects names, email, resume text and children's learning data
and is marketed to students, with zero legal surface -- not a stub. These
tests pin that the two pages exist, are public and crawlable, and that
signup surfaces them.
"""

from __future__ import annotations

import pytest


@pytest.mark.parametrize("path", ["/terms", "/privacy"])
async def test_legal_pages_are_public_and_render(client, path):
    r = await client.get(path)
    assert r.status_code == 200
    assert "Last updated" in r.text


async def test_the_two_pages_link_to_each_other(client):
    terms = await client.get("/terms")
    privacy = await client.get("/privacy")
    assert 'href="/privacy"' in terms.text
    assert 'href="/terms"' in privacy.text


async def test_landing_footer_links_to_both(client):
    r = await client.get("/")
    assert 'href="/terms"' in r.text
    assert 'href="/privacy"' in r.text


async def test_signup_page_surfaces_the_terms(client):
    r = await client.get("/signup")
    assert 'href="/terms"' in r.text
    assert 'href="/privacy"' in r.text


async def test_robots_allows_the_legal_pages(client):
    r = await client.get("/robots.txt")
    assert "Allow: /terms" in r.text
    assert "Allow: /privacy" in r.text
