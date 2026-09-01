from __future__ import annotations

import asyncpg

DOC_TYPES = ("resume", "transcript", "certificate", "cover_letter", "other")


class DocumentNotFound(Exception):
    pass


async def create_document(
    pool: asyncpg.Pool,
    user_id: int,
    *,
    doc_type: str,
    title: str,
    filename: str,
    mime_type: str,
    content_bytes: bytes,
    extracted_text: str,
) -> int:
    return await pool.fetchval(
        """INSERT INTO documents
               (user_id, doc_type, title, filename, mime_type, size_bytes, content_bytes, extracted_text)
           VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
           RETURNING document_id""",
        user_id,
        doc_type,
        title,
        filename,
        mime_type,
        len(content_bytes),
        content_bytes,
        extracted_text,
    )


async def list_documents(
    pool: asyncpg.Pool,
    user_id: int,
    *,
    doc_type: str | None = None,
    favorites_only: bool = False,
) -> list[asyncpg.Record]:
    """Metadata only -- never pulls content_bytes (or the full
    extracted_text, just whether it's non-empty) for a list view.

    Phase 4.3 (library UI), narrowed: favorites + type filtering are the
    two pieces of real value for a vault this app's own scale actually
    has (a handful to a few dozen files per student, not thousands) --
    rigid folders and a full semantic-search index are more machinery
    than a vault this size needs. Favorites sort first, so pinning
    something actually surfaces it instead of just flagging it."""
    conditions = ["user_id = $1"]
    params: list = [user_id]

    if doc_type:
        params.append(doc_type)
        conditions.append(f"doc_type = ${len(params)}")
    if favorites_only:
        conditions.append("is_favorite = true")

    where_clause = " AND ".join(conditions)
    return await pool.fetch(
        f"""SELECT document_id, doc_type, title, filename, mime_type, size_bytes, created_at,
                   is_favorite, (extracted_text != '') AS has_extracted_text
            FROM documents WHERE {where_clause}
            ORDER BY is_favorite DESC, created_at DESC""",
        *params,
    )


async def toggle_favorite(pool: asyncpg.Pool, user_id: int, document_id: int) -> bool:
    """Ownership-checked flip of is_favorite; returns the new state."""
    row = await pool.fetchrow(
        """UPDATE documents SET is_favorite = NOT is_favorite
           WHERE user_id = $1 AND document_id = $2
           RETURNING is_favorite""",
        user_id,
        document_id,
    )
    if row is None:
        raise DocumentNotFound(document_id)
    return row["is_favorite"]


async def get_document_for_download(pool: asyncpg.Pool, user_id: int, document_id: int) -> asyncpg.Record:
    """Ownership-checked fetch including the actual file bytes."""
    row = await pool.fetchrow(
        """SELECT document_id, filename, mime_type, content_bytes
           FROM documents WHERE user_id = $1 AND document_id = $2""",
        user_id,
        document_id,
    )
    if row is None:
        raise DocumentNotFound(document_id)
    return row


async def get_document_for_flashcards(pool: asyncpg.Pool, user_id: int, document_id: int) -> asyncpg.Record:
    """Ownership-checked fetch of just what generating flashcards needs --
    title and extracted text, never the raw file bytes (no reason to pull
    a multi-MB PDF blob into memory just to read the text already
    extracted from it at upload time)."""
    row = await pool.fetchrow(
        """SELECT document_id, title, extracted_text
           FROM documents WHERE user_id = $1 AND document_id = $2""",
        user_id,
        document_id,
    )
    if row is None:
        raise DocumentNotFound(document_id)
    return row


async def delete_document(pool: asyncpg.Pool, user_id: int, document_id: int) -> None:
    row = await pool.fetchrow(
        "SELECT document_id FROM documents WHERE user_id = $1 AND document_id = $2", user_id, document_id
    )
    if row is None:
        raise DocumentNotFound(document_id)
    await pool.execute("DELETE FROM documents WHERE user_id = $1 AND document_id = $2", user_id, document_id)
