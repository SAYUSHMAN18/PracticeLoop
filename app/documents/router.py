from __future__ import annotations

import re

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from app.core.db import get_pool
from app.core.deps import inject_current_user, require_user_id
from app.core.templates import templates
from app.documents import service
from app.profile import service as profile_service

router = APIRouter(dependencies=[Depends(inject_current_user)])

MAX_DOCUMENT_BYTES = 10 * 1024 * 1024  # 10MB, matches the resume-upload cap

DOC_TYPE_LABELS = {
    "resume": "Resume",
    "transcript": "Transcript",
    "certificate": "Certificate",
    "cover_letter": "Cover letter",
    "other": "Other",
}

# Strips characters that would let a filename or content-type break out of
# its header line (CRLF response-splitting, stray quotes) -- both come
# straight from the uploaded file, so neither is trusted as-is.
_HEADER_UNSAFE_RE = re.compile(r'[\r\n"]')


def _safe_header_value(value: str, fallback: str) -> str:
    cleaned = _HEADER_UNSAFE_RE.sub("", value).strip()
    return cleaned or fallback


def _format_size(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes} B"
    return f"{size_bytes / 1024:.1f} KB"


async def _render_index(request: Request, pool, user_id: int, *, error: str | None, status_code: int = 200):
    rows = await service.list_documents(pool, user_id)
    documents = [{**dict(row), "size_display": _format_size(row["size_bytes"])} for row in rows]
    return templates.TemplateResponse(
        request,
        "documents/index.html",
        {"documents": documents, "doc_type_labels": DOC_TYPE_LABELS, "error": error},
        status_code=status_code,
    )


@router.get("/documents", response_class=HTMLResponse)
async def documents_page(request: Request, user_id: int = Depends(require_user_id), pool=Depends(get_pool)):
    return await _render_index(request, pool, user_id, error=None)


@router.post("/documents")
async def upload_document(
    request: Request,
    doc_type: str = Form("other"),
    title: str = Form(""),
    file: UploadFile = File(...),
    user_id: int = Depends(require_user_id),
    pool=Depends(get_pool),
):
    if doc_type not in service.DOC_TYPES:
        doc_type = "other"

    # Read one byte past the cap so an oversized upload is detected without
    # ever holding the whole (possibly huge) file in memory -- same pattern
    # as the profile resume upload.
    content = await file.read(MAX_DOCUMENT_BYTES + 1)
    if len(content) > MAX_DOCUMENT_BYTES:
        raise HTTPException(status_code=413, detail="File is too large (max 10MB).")
    if not content:
        return await _render_index(request, pool, user_id, error="That file looks empty.", status_code=400)

    extracted_text = ""
    try:
        extracted_text = profile_service.extract_resume_text(file.filename or "upload", content)
    except Exception:
        extracted_text = ""  # non-text formats (images, scans) just skip extraction; storage still works

    display_title = title.strip() or file.filename or DOC_TYPE_LABELS.get(doc_type, "Document")

    await service.create_document(
        pool,
        user_id,
        doc_type=doc_type,
        title=display_title,
        filename=file.filename or "document",
        mime_type=file.content_type or "application/octet-stream",
        content_bytes=content,
        extracted_text=extracted_text,
    )

    # A resume-tagged upload also becomes the active resume used by job-fit
    # scoring, gap analysis, and tailoring -- one upload, wired everywhere,
    # instead of making the student re-paste it on the profile page too.
    if doc_type == "resume" and extracted_text:
        profile = await profile_service.get_profile(pool, user_id)
        await profile_service.update_profile(
            pool,
            user_id,
            profile["target_role"],
            profile["target_companies"],
            extracted_text,
            goal_type=profile["goal_type"],
            target_date=profile["target_date"],
            daily_time_budget_minutes=profile["daily_time_budget_minutes"],
            timezone=profile["timezone"],
        )

    return await _render_index(request, pool, user_id, error=None)


@router.get("/documents/{document_id}/download")
async def download_document(
    document_id: int, user_id: int = Depends(require_user_id), pool=Depends(get_pool)
):
    try:
        document = await service.get_document_for_download(pool, user_id, document_id)
    except service.DocumentNotFound as exc:
        raise HTTPException(status_code=404) from exc

    filename = _safe_header_value(document["filename"], "document")
    mime_type = _safe_header_value(document["mime_type"], "application/octet-stream")
    return Response(
        content=bytes(document["content_bytes"]),
        media_type=mime_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/documents/{document_id}/delete")
async def delete_document(document_id: int, user_id: int = Depends(require_user_id), pool=Depends(get_pool)):
    try:
        await service.delete_document(pool, user_id, document_id)
    except service.DocumentNotFound as exc:
        raise HTTPException(status_code=404) from exc
    return RedirectResponse("/documents", status_code=303)
