from __future__ import annotations

from pathlib import Path

from fastapi.templating import Jinja2Templates

_APP_DIR = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = _APP_DIR / "templates"
STATIC_DIR = _APP_DIR / "static"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
