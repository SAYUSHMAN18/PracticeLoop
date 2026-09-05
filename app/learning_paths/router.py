from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.core.db import get_pool
from app.core.deps import inject_current_user, require_user_id
from app.core.llm import is_configured as llm_is_configured
from app.core.llm_budget import require_llm_budget
from app.core.markdown import render_markdown
from app.core.templates import templates
from app.learning_paths import service

# Rendered for display only -- render_markdown's output never gets written
# back to the DB, so the stored content column stays plain text/LaTeX
# source. emphasis=False for the same reason Math Lab needs it: a lesson
# on algebra saying "2*x = 6" would otherwise have its multiplication sign
# eaten by markdown pairing the two asterisks into <em>. "example" is
# deliberately left alone -- it renders inside a <pre class="code-block">
# as literal preformatted text, which may be real source code, not prose.
_MARKDOWN_LESSON_FIELDS = ("concept", "checkpoint_question", "checkpoint_answer", "summary")

router = APIRouter(dependencies=[Depends(inject_current_user)])


@router.get("/learning-paths", response_class=HTMLResponse)
async def learning_paths_index(
    request: Request,
    user_id: int = Depends(require_user_id),
    pool=Depends(get_pool),
):
    paths = await service.list_paths(pool, user_id)
    return templates.TemplateResponse(
        request,
        "learning_paths/index.html",
        {"paths": paths, "error": None},
    )


@router.post("/learning-paths")
async def create_learning_path(
    request: Request,
    goal: str = Form(...),
    user_id: int = Depends(require_user_id),
    pool=Depends(get_pool),
    _budget: None = Depends(require_llm_budget),
):
    goal = goal.strip()
    if not goal:
        paths = await service.list_paths(pool, user_id)
        return templates.TemplateResponse(
            request,
            "learning_paths/index.html",
            {"paths": paths, "error": "Tell us what you're working toward first."},
            status_code=400,
        )

    path_id = await service.create_path(pool, user_id, goal, ai_available=llm_is_configured())
    return RedirectResponse(f"/learning-paths/{path_id}", status_code=303)


@router.get("/learning-paths/{path_id}", response_class=HTMLResponse)
async def learning_path_detail(
    path_id: int,
    request: Request,
    user_id: int = Depends(require_user_id),
    pool=Depends(get_pool),
):
    try:
        path = await service.get_path_detail(pool, user_id, path_id)
    except service.PathNotFound as exc:
        raise HTTPException(status_code=404) from exc
    return templates.TemplateResponse(request, "learning_paths/detail.html", {"path": path})


@router.get("/learning-paths/{path_id}/lessons/{lesson_id}", response_class=HTMLResponse)
async def lesson_detail(
    path_id: int,
    lesson_id: int,
    request: Request,
    user_id: int = Depends(require_user_id),
    pool=Depends(get_pool),
):
    try:
        lesson = await service.get_lesson(pool, user_id, path_id, lesson_id, ai_available=llm_is_configured())
    except service.PathNotFound as exc:
        raise HTTPException(status_code=404) from exc
    for field in _MARKDOWN_LESSON_FIELDS:
        if lesson["content"].get(field):
            lesson["content"][field] = render_markdown(lesson["content"][field], emphasis=False)
    return templates.TemplateResponse(request, "learning_paths/lesson.html", {"lesson": lesson})


@router.post("/learning-paths/{path_id}/delete")
async def delete_learning_path(path_id: int, user_id: int = Depends(require_user_id), pool=Depends(get_pool)):
    try:
        await service.delete_path(pool, user_id, path_id)
    except service.PathNotFound as exc:
        raise HTTPException(status_code=404) from exc
    return RedirectResponse("/learning-paths", status_code=303)


@router.post("/learning-paths/{path_id}/lessons/{lesson_id}/toggle")
async def toggle_lesson(
    path_id: int,
    lesson_id: int,
    next: str = Form(""),
    user_id: int = Depends(require_user_id),
    pool=Depends(get_pool),
):
    """Toggled both from the path's own lesson-tree view and from a
    lesson's own page -- `next` sends the student back to whichever one
    they toggled it from. Constrained to this exact path's own URLs (not
    an arbitrary redirect target) so a crafted `next` can't be used to
    bounce a logged-in student somewhere else."""
    try:
        await service.toggle_lesson(pool, user_id, path_id, lesson_id)
    except service.PathNotFound as exc:
        raise HTTPException(status_code=404) from exc

    path_prefix = f"/learning-paths/{path_id}"
    destination = next if next.startswith(path_prefix + "/") or next == path_prefix else path_prefix
    return RedirectResponse(destination, status_code=303)


@router.get("/subjects", response_class=HTMLResponse)
async def subjects_index(request: Request, user_id: int = Depends(require_user_id)):
    return templates.TemplateResponse(
        request, "learning_paths/subjects.html", {"templates": service.TEMPLATES}
    )


@router.post("/subjects/{template_id}/start")
async def start_from_template(
    template_id: str,
    user_id: int = Depends(require_user_id),
    pool=Depends(get_pool),
    _budget: None = Depends(require_llm_budget),
):
    template = service.get_template(template_id)
    if template is None:
        raise HTTPException(status_code=404)

    path_id = await service.create_path(
        pool,
        user_id,
        template["title"],
        ai_available=llm_is_configured(),
        source_type="template",
        source_detail=template_id,
    )
    return RedirectResponse(f"/learning-paths/{path_id}", status_code=303)
