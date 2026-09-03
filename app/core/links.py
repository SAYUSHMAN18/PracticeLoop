"""Validate a user-supplied profile/project link.

Stored as plain text, rendered as `<a href>` on the portfolio, so the
rules are: it must parse as an absolute http(s) URL with a real-looking
host, and where a specific host is expected (github.com, linkedin.com) it
must be on that host or a subdomain. Anything that fails comes back as
"" -- the caller stores that instead of raising, so a fat-fingered link
just doesn't save rather than blocking the whole form.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

_MAX = 300
_HOST_RE = re.compile(r"^[a-z0-9.-]+\.[a-z]{2,}$", re.I)  # something.tld, no spaces


def clean_url(raw: str, *, host_contains: str = "") -> str:
    raw = (raw or "").strip()
    if not raw or len(raw) > _MAX:
        return ""
    # Tolerate "github.com/me" -- but only if it already looks host-shaped,
    # so "javascript:alert(1)" or "not a url" don't get an https:// prefix.
    if "://" not in raw:
        head = raw.split("/", 1)[0]
        if not _HOST_RE.match(head):
            return ""
        raw = "https://" + raw

    try:
        parts = urlparse(raw)
        host = parts.hostname or ""
        port = parts.port  # can raise ValueError on a junk port
    except ValueError:
        return ""

    if parts.scheme not in ("http", "https") or not _HOST_RE.match(host):
        return ""
    if host_contains and host_contains.lower() not in host.lower():
        return ""

    netloc = host + (f":{port}" if port else "")
    path = parts.path.rstrip("/")
    query = f"?{parts.query}" if parts.query else ""
    return f"https://{netloc}{path}{query}"
