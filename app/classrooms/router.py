from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.classrooms import service
from app.core.db import get_pool
from app.core.deps import inject_current_user, require_user_id
from app.core.templates import templates

router = APIRouter(prefix="/classrooms", dependencies=[Depends(inject_current_user)])


def _require_teacher(request: Request) -> None:
    cu = request.state.current_user
    if cu is None or cu["role"] != "teacher":
        raise HTTPException(status_code=403, detail="Switch to teacher mode on your profile first.")


async def _render_index(request: Request, pool, user_id: int, *, error: str | None, status_code: int = 200):
    is_teacher = request.state.current_user is not None and request.state.current_user["role"] == "teacher"
    owned = await service.list_classrooms_for_teacher(pool, user_id) if is_teacher else []
    joined = await service.list_classrooms_for_student(pool, user_id)
    assignments = await service.list_assignments_for_student(pool, user_id)
    return templates.TemplateResponse(
        request,
        "classrooms/index.html",
        {
            "is_teacher": is_teacher,
            "owned": owned,
            "joined": joined,
            "assignments": assignments,
            "error": error,
        },
        status_code=status_code,
    )


@router.get("", response_class=HTMLResponse)
async def classrooms_index(
    request: Request,
    user_id: int = Depends(require_user_id),
    pool=Depends(get_pool),
):
    return await _render_index(request, pool, user_id, error=None)


@router.post("")
async def create_classroom(
    request: Request,
    name: str = Form(...),
    user_id: int = Depends(require_user_id),
    pool=Depends(get_pool),
):
    _require_teacher(request)
    name = name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Give the classroom a name.")
    created = await service.create_classroom(pool, user_id, name)
    return RedirectResponse(f"/classrooms/{created['classroom_id']}", status_code=303)


@router.post("/join")
async def join_classroom(
    request: Request,
    join_code: str = Form(...),
    user_id: int = Depends(require_user_id),
    pool=Depends(get_pool),
):
    try:
        await service.join_classroom(pool, user_id, join_code)
    except service.InvalidJoinCode:
        return await _render_index(
            request, pool, user_id, error="That join code doesn't match a classroom.", status_code=400
        )
    return RedirectResponse("/classrooms", status_code=303)


@router.get("/{classroom_id}", response_class=HTMLResponse)
async def classroom_detail(
    classroom_id: int,
    request: Request,
    user_id: int = Depends(require_user_id),
    pool=Depends(get_pool),
):
    try:
        classroom = await service.get_classroom_for_teacher(pool, user_id, classroom_id)
        roster = await service.get_roster(pool, user_id, classroom_id)
        assignments = await service.list_assignments_for_classroom(pool, user_id, classroom_id)
    except service.ClassroomNotFound as exc:
        raise HTTPException(status_code=404) from exc
    return templates.TemplateResponse(
        request,
        "classrooms/detail.html",
        {"classroom": classroom, "roster": roster, "assignments": assignments},
    )


@router.post("/{classroom_id}/assignments")
async def create_assignment(
    classroom_id: int,
    title: str = Form(...),
    description: str = Form(""),
    topic: str = Form(""),
    due_date: str = Form(""),
    user_id: int = Depends(require_user_id),
    pool=Depends(get_pool),
):
    title = title.strip()
    if not title:
        raise HTTPException(status_code=400, detail="Give the assignment a title.")
    parsed_due = None
    if due_date.strip():
        try:
            parsed_due = date.fromisoformat(due_date.strip())
        except ValueError:
            parsed_due = None
    try:
        await service.create_assignment(
            pool, user_id, classroom_id, title, description.strip(), topic.strip(), parsed_due
        )
    except service.ClassroomNotFound as exc:
        raise HTTPException(status_code=404) from exc
    return RedirectResponse(f"/classrooms/{classroom_id}", status_code=303)


@router.post("/{classroom_id}/delete")
async def delete_classroom(
    classroom_id: int, user_id: int = Depends(require_user_id), pool=Depends(get_pool)
):
    try:
        await service.delete_classroom(pool, user_id, classroom_id)
    except service.ClassroomNotFound as exc:
        raise HTTPException(status_code=404) from exc
    return RedirectResponse("/classrooms", status_code=303)
