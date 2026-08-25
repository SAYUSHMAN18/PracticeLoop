from __future__ import annotations

import asyncpg


async def get_profile(pool: asyncpg.Pool, user_id: int) -> asyncpg.Record:
    return await pool.fetchrow(
        "SELECT user_id, target_role, target_companies, resume_text FROM profiles WHERE user_id = $1",
        user_id,
    )


async def update_profile(
    pool: asyncpg.Pool,
    user_id: int,
    target_role: str,
    target_companies: str,
    resume_text: str | None = None,
) -> None:
    if resume_text is None:
        await pool.execute(
            """UPDATE profiles SET target_role = $2, target_companies = $3, updated_at = now()
               WHERE user_id = $1""",
            user_id,
            target_role,
            target_companies,
        )
    else:
        await pool.execute(
            """UPDATE profiles
               SET target_role = $2, target_companies = $3, resume_text = $4, updated_at = now()
               WHERE user_id = $1""",
            user_id,
            target_role,
            target_companies,
            resume_text,
        )


def extract_resume_text(filename: str, content: bytes) -> str:
    """Deterministic text extraction -- no LLM call needed for this step."""
    if filename.lower().endswith(".pdf"):
        from io import BytesIO

        from pypdf import PdfReader

        reader = PdfReader(BytesIO(content))
        return "\n".join(page.extract_text() or "" for page in reader.pages).strip()

    return content.decode("utf-8", errors="ignore").strip()
