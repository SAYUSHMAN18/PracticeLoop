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


async def list_documents(pool: asyncpg.Pool, user_id: int) -> list[asyncpg.Record]:
    """Metadata only -- never pulls content_bytes for a list view."""
    return await pool.fetch(
        """SELECT document_id, doc_type, title, filename, mime_type, size_bytes, created_at
           FROM documents WHERE user_id = $1 ORDER BY created_at DESC""",
        user_id,
    )


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


async def delete_document(pool: asyncpg.Pool, user_id: int, document_id: int) -> None:
    row = await pool.fetchrow(
        "SELECT document_id FROM documents WHERE user_id = $1 AND document_id = $2", user_id, document_id
    )
    if row is None:
        raise DocumentNotFound(document_id)
    await pool.execute("DELETE FROM documents WHERE user_id = $1 AND document_id = $2", user_id, document_id)
