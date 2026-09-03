from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.core.db import get_pool
from app.core.deps import inject_current_user, require_user_id
from app.core.llm import is_configured as llm_is_configured
from app.core.llm_budget import require_llm_budget
from app.core.templates import templates
from app.profile.service import PROFICIENCY_LABELS
from app.projects import service

router = APIRouter(dependencies=[Depends(inject_current_user)])


@router.get("/projects", response_class=HTMLResponse)
async def projects_index(
    request: Request,
    user_id: int = Depends(require_user_id),
    pool=Depends(get_pool),
):
    projects = await service.list_projects(pool, user_id)
    return templates.TemplateResponse(request, "projects/index.html", {"projects": projects, "error": None})


@router.post("/projects")
async def create_project(
    request: Request,
    topic: str = Form(...),
    user_id: int = Depends(require_user_id),
    pool=Depends(get_pool),
    _budget: None = Depends(require_llm_budget),
):
    topic = topic.strip()
    if not topic:
        projects = await service.list_projects(pool, user_id)
        return templates.TemplateResponse(
            request,
            "projects/index.html",
            {"projects": projects, "error": "Tell us what kind of project you're looking for first."},
            status_code=400,
        )

    idea = await service.generate_idea(topic, ai_available=llm_is_configured())
    project_id = await service.create_project(pool, user_id, idea["title"], idea["brief"], idea["milestones"])
    return RedirectResponse(f"/projects/{project_id}", status_code=303)


@router.get("/projects/{project_id}", response_class=HTMLResponse)
async def project_detail(
    project_id: int,
    request: Request,
    user_id: int = Depends(require_user_id),
    pool=Depends(get_pool),
):
    try:
        project = await service.get_project(pool, user_id, project_id)
    except service.ProjectNotFound as exc:
        raise HTTPException(status_code=404) from exc
    return templates.TemplateResponse(request, "projects/detail.html", {"project": project})


@router.post("/projects/{project_id}/repo")
async def set_project_repo(
    project_id: int,
    repo_url: str = Form(""),
    user_id: int = Depends(require_user_id),
    pool=Depends(get_pool),
):
    from app.core.links import clean_url

    try:
        await service.set_project_repo(pool, user_id, project_id, clean_url(repo_url))
    except service.ProjectNotFound as exc:
        raise HTTPException(status_code=404) from exc
    return RedirectResponse(f"/projects/{project_id}", status_code=303)


@router.post("/projects/{project_id}/milestones/{milestone_id}/toggle")
async def toggle_milestone(
    project_id: int,
    milestone_id: int,
    user_id: int = Depends(require_user_id),
    pool=Depends(get_pool),
):
    try:
        await service.toggle_milestone(pool, user_id, project_id, milestone_id)
    except service.ProjectNotFound as exc:
        raise HTTPException(status_code=404) from exc
    return RedirectResponse(f"/projects/{project_id}", status_code=303)


@router.post("/projects/{project_id}/submit")
async def submit_project(
    project_id: int,
    submission_text: str = Form(...),
    submission_link: str = Form(""),
    user_id: int = Depends(require_user_id),
    pool=Depends(get_pool),
):
    if not submission_text.strip():
        raise HTTPException(status_code=400, detail="Describe what you built before submitting.")
    try:
        await service.submit_project(
            pool,
            user_id,
            project_id,
            submission_text.strip(),
            submission_link.strip(),
            ai_available=llm_is_configured(),
        )
    except service.ProjectNotFound as exc:
        raise HTTPException(status_code=404) from exc
    return RedirectResponse(f"/projects/{project_id}", status_code=303)


@router.get("/portfolio", response_class=HTMLResponse)
async def portfolio(
    request: Request,
    user_id: int = Depends(require_user_id),
    pool=Depends(get_pool),
):
    streak = request.state.current_streak or 0
    data = await service.get_portfolio(pool, user_id, streak_days=streak)
    return templates.TemplateResponse(
        request, "portfolio/index.html", {**data, "proficiency_labels": PROFICIENCY_LABELS}
    )


@router.post("/projects/{project_id}/delete")
async def delete_project(project_id: int, user_id: int = Depends(require_user_id), pool=Depends(get_pool)):
    try:
        await service.delete_project(pool, user_id, project_id)
    except service.ProjectNotFound as exc:
        raise HTTPException(status_code=404) from exc
    return RedirectResponse("/projects", status_code=303)
