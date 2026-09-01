from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from app.core.deps import inject_current_user, require_user_id
from app.core.templates import templates

router = APIRouter(dependencies=[Depends(inject_current_user)])

# Phase 5 (app shell) added these sections to the sidebar's new
# information architecture ahead of the phases that actually build them
# out. A real destination that says what's coming beats either a dead
# link or hiding the nav item until the feature exists.
#
# My Learning Paths, Explore Subjects (Phase 6), Assessments (Phase 9),
# and Projects (Phase 13) all moved out of this file once they became
# real pages. Progress (Phase 15) is still ahead, so it stays here as a
# stub.
_PROGRESS = {
    "title": "Progress",
    "blurb": (
        "One place for mastery trends, retention, learning time, and explainable "
        "recommendations, instead of scattered stats."
    ),
    "phase": "Phase 15 — Analytics and Progress Intelligence",
    "coming": [
        "Mastery-by-skill and accuracy/retention trends over time",
        "A full learning history timeline",
        "“Why this was recommended” explanations, not just a suggestion",
    ],
}


@router.get("/progress", response_class=HTMLResponse)
async def progress(request: Request, user_id: int = Depends(require_user_id)):
    return templates.TemplateResponse(request, "roadmap/coming_soon.html", _PROGRESS)
