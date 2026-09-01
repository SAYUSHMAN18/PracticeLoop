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
# My Learning Paths, Explore Subjects (Phase 6), and Assessments
# (Phase 9) moved out of this file once they became real pages --
# app/learning_paths/router.py and app/assessments/router.py. Projects
# (Phase 13) and Progress (Phase 15) are still ahead, so they stay here
# as stubs.
_PROJECTS = {
    "title": "Projects",
    "blurb": (
        "Guided and independent projects with rubrics, milestones, and mentor feedback -- "
        "real output beyond a mastery score."
    ),
    "phase": "Phase 13 — Projects and Proof of Learning",
    "coming": [
        "Guided projects tied to a learning path's units",
        "Milestones, rubric-based feedback, and a final submission",
        "A shareable skill portfolio built from completed projects",
    ],
}
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


@router.get("/projects", response_class=HTMLResponse)
async def projects(request: Request, user_id: int = Depends(require_user_id)):
    return templates.TemplateResponse(request, "roadmap/coming_soon.html", _PROJECTS)


@router.get("/progress", response_class=HTMLResponse)
async def progress(request: Request, user_id: int = Depends(require_user_id)):
    return templates.TemplateResponse(request, "roadmap/coming_soon.html", _PROGRESS)
