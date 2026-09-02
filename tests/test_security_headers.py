"""CSP nonce behaviour.

The whole value of moving script-src off 'unsafe-inline' is that injected
markup can't execute. That only holds if every inline <script> this app
renders carries the current request's nonce, the nonce is unguessable, and
it changes per request -- a template that forgets one would either break
that page's JavaScript or (worse) push someone to put 'unsafe-inline' back.
"""

from __future__ import annotations

import re

from tests.conftest import signup

_NONCE_RE = re.compile(r"'nonce-([A-Za-z0-9_-]+)'")
# <script> that has neither a nonce= nor a src= -- i.e. inline and unnonced.
_UNNONCED_RE = re.compile(r"<script(?![^>]*(?:nonce=|src=))")


def _nonce(response) -> str:
    match = _NONCE_RE.search(response.headers["content-security-policy"])
    assert match, response.headers["content-security-policy"]
    return match.group(1)


async def test_script_src_is_nonce_based_and_not_unsafe_inline(client):
    csp = (await client.get("/login")).headers["content-security-policy"]
    script_src = next(d for d in csp.split("; ") if d.startswith("script-src"))
    assert "'unsafe-inline'" not in script_src, script_src
    assert "'nonce-" in script_src, script_src


async def test_no_third_party_origins_are_allowed_anywhere_in_the_policy(client):
    """htmx and the webfonts are self-hosted; nothing should re-introduce a
    CDN into the policy without this failing first."""
    csp = (await client.get("/login")).headers["content-security-policy"]
    assert "https://" not in csp, csp


async def test_every_inline_script_carries_the_requests_nonce(client):
    await signup(client, "csp-nonce@example.com")

    for path in ["/dashboard", "/account", "/documents", "/learning-paths", "/practice/review"]:
        response = await client.get(path)
        nonce = _nonce(response)
        rendered = set(re.findall(r'<script nonce="([^"]+)"', response.text))
        assert not _UNNONCED_RE.search(response.text), f"{path} has an inline script with no nonce"
        assert rendered <= {nonce}, f"{path} rendered a nonce that isn't this response's"


async def test_the_nonce_is_different_on_every_request(client):
    first = _nonce(await client.get("/login"))
    second = _nonce(await client.get("/login"))
    assert first != second
    # token_urlsafe(16) -- a short/predictable nonce is worth as much to an
    # attacker as 'unsafe-inline' was.
    assert len(first) >= 20


async def test_logged_out_pages_are_covered_too(client):
    """base_auth.html is a separate layout from base.html -- it has its own
    inline scripts and its own chance to miss the nonce."""
    response = await client.get("/signup")
    assert not _UNNONCED_RE.search(response.text)
    assert f'nonce="{_nonce(response)}"' in response.text
