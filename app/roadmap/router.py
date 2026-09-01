from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from app.core.deps import inject_current_user, require_user_id
from app.core.templates import templates

router = APIRouter(dependencies=[Depends(inject_current_user)])

# Phase 5 (app shell) adds these five sections to the sidebar's new
# information architecture ahead of the phases that actually build them
# out (Learning Paths/Explore Subjects in Phase 6, Assessments in Phase
# 9, Projects in Phase 13, Progress in Phase 15). A real destination that
# says what's coming beats either a dead link or hiding the nav item
# until the feature exists -- the whole point of shipping the shell
# first is that the information architecture is visible and navigable
# from day one, even where the pages behind it are still stubs.
_LEARNING_PATHS = {
    "title": "My Learning Paths",
    "blurb": (
        "Turn a goal, a syllabus, an uploaded document, or a job description into a "
        "structured path of modules, units, and lessons -- with progress, mastery, and "
        "a next recommended action for each one."
    ),
    "phase": "Phase 6 — Learning Paths and Course Architecture",
    "coming": [
        "Create a path from a goal, subject, syllabus, or document",
        "Modules → units → lessons, each with a mastery level",
        "Progress %, estimated completion time, and weak areas per path",
    ],
}
_SUBJECTS = {
    "title": "Explore Subjects",
    "blurb": (
        "Browse existing PracticeLoop templates and subjects to start a learning path from, "
        "without starting from a blank goal."
    ),
    "phase": "Phase 6 — Learning Paths and Course Architecture",
    "coming": [
        "A catalog of ready-made subject and exam templates",
        "One click to turn a template into your own learning path",
    ],
}
_ASSESSMENTS = {
    "title": "Assessments",
    "blurb": (
        "A short adaptive diagnostic before you start a path, plus quizzes and mastery "
        "checkpoints along the way -- so your starting level and progress are measured, "
        "not just assumed."
    ),
    "phase": "Phase 9 — Diagnostic and Adaptive Learning",
    "coming": [
        "Quick (5-10q), standard (15-25q), or deep (30-50q) diagnostics",
        "Difficulty adapts to your answers as you go",
        "A weakness map and a recommended starting unit at the end",
    ],
}
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


@router.get("/learning-paths", response_class=HTMLResponse)
async def learning_paths(request: Request, user_id: int = Depends(require_user_id)):
    return templates.TemplateResponse(request, "roadmap/coming_soon.html", _LEARNING_PATHS)


@router.get("/subjects", response_class=HTMLResponse)
async def subjects(request: Request, user_id: int = Depends(require_user_id)):
    return templates.TemplateResponse(request, "roadmap/coming_soon.html", _SUBJECTS)


@router.get("/assessments", response_class=HTMLResponse)
async def assessments(request: Request, user_id: int = Depends(require_user_id)):
    return templates.TemplateResponse(request, "roadmap/coming_soon.html", _ASSESSMENTS)


@router.get("/projects", response_class=HTMLResponse)
async def projects(request: Request, user_id: int = Depends(require_user_id)):
    return templates.TemplateResponse(request, "roadmap/coming_soon.html", _PROJECTS)


@router.get("/progress", response_class=HTMLResponse)
async def progress(request: Request, user_id: int = Depends(require_user_id)):
    return templates.TemplateResponse(request, "roadmap/coming_soon.html", _PROGRESS)
