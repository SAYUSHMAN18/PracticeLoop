from __future__ import annotations

import hashlib
from functools import cache
from pathlib import Path

from fastapi.templating import Jinja2Templates

from app.core.config import settings

_APP_DIR = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = _APP_DIR / "templates"
STATIC_DIR = _APP_DIR / "static"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


@cache
def _static_asset_version(filename: str) -> str:
    """A short hash of one static file's own content, appended by templates
    as a query string (?v=...). Cached, so each file is hashed once per
    process rather than re-read on every page render.

    Browsers cache /static/* aggressively -- StaticCacheHeadersMiddleware
    marks it `immutable` for a year -- so a deploy that changes a file but
    not its URL can leave a visitor's browser serving the old one against
    new HTML. That already happened once here: new sidebar markup, stale
    cached CSS, so every sidebar rule simply didn't exist as far as the
    browser was concerned. Hashing the content means the URL changes
    exactly when the file does, and stays put (full cache reuse) when it
    doesn't.

    Per-file, not one hash for everything: htmx and style.css change on
    completely different schedules, and a shared hash would both re-fetch
    a 50KB script for a one-line CSS tweak and -- much worse -- fail to
    bust htmx's own year-long cache entry on an htmx upgrade that left the
    CSS untouched.
    """
    try:
        digest = hashlib.sha256((STATIC_DIR / filename).read_bytes()).hexdigest()
    except OSError:
        # A missing static file is a startup problem for elsewhere to
        # raise; degrading to an unversioned URL beats a 500 on every page.
        return "0"
    return digest[:10]


templates.env.globals["asset_version"] = _static_asset_version
# The absolute origin this site is served from. Templates build canonical
# and Open Graph URLs off it, so no route has to pass one down by hand.
templates.env.globals["public_base_url"] = settings.public_base_url.rstrip("/")
# Whether to show the "Continue with Google" button at all -- login.html
# and signup.html both need this, and a global (like public_base_url
# above) means neither route has to remember to thread it through every
# one of their own error-path context dicts. A callable, not a value
# snapshotted once at import time: tests monkeypatch settings at runtime
# and need the button's visibility to actually track that.
templates.env.globals["google_oauth_available"] = lambda: bool(
    settings.google_oauth_client_id.strip() and settings.google_oauth_client_secret.strip()
)


def _highlight_filter(code: str | None, language: str = "") -> str:
    """`{{ snippet | highlight(lang) }}` -> Pygments HTML. Returns Markup
    (via highlight_code) so Jinja doesn't re-escape the spans."""
    from app.core.highlighting import highlight_code

    return highlight_code(code or "", language or "")


templates.env.filters["highlight"] = _highlight_filter
