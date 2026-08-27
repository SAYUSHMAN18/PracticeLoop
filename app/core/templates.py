from __future__ import annotations

import hashlib
from pathlib import Path

from fastapi.templating import Jinja2Templates

_APP_DIR = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = _APP_DIR / "templates"
STATIC_DIR = _APP_DIR / "static"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def _static_asset_version() -> str:
    """A short hash of style.css's own content, computed once at import
    time. Browsers cache /static/style.css aggressively with no explicit
    Cache-Control from StaticFiles, so a deploy that changes the CSS but
    not the URL can leave a visitor's browser silently serving the old
    file against the new HTML -- exactly what happened here: new sidebar
    markup, stale cached CSS, so every sidebar-specific rule just didn't
    exist as far as the browser was concerned. Templates append this as
    a query string (?v=...) so the URL itself changes whenever the file's
    content does, forcing a fresh fetch -- and stays the same (full cache
    reuse) across deploys that don't touch the CSS."""
    css_path = STATIC_DIR / "style.css"
    try:
        digest = hashlib.sha256(css_path.read_bytes()).hexdigest()
    except OSError:
        return "0"  # style.css missing is a startup-time problem elsewhere, not this function's to raise
    return digest[:10]


templates.env.globals["asset_version"] = _static_asset_version()
