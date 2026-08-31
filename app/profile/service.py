from __future__ import annotations

from datetime import date

import asyncpg

# Phase 2.3's goal list trimmed to what this app can actually act on today
# (practice + jobs, not assignments/certifications/language learning) --
# expand this alongside whatever feature would actually use a new one.
# Lives here rather than in the router so other routers (the dashboard's
# goal-countdown banner) can import the label mapping without importing
# one route module from another.
GOAL_TYPE_LABELS = {
    "": "Not set",
    "learn_from_scratch": "Learn a subject from the beginning",
    "exam_prep": "Prepare for an exam",
    "improve_weak_areas": "Improve weak concepts",
    "interview_prep": "Prepare for an interview",
    "build_project": "Build a project",
    "revise_before_deadline": "Revise before a deadline",
    "maintain_knowledge": "Maintain long-term knowledge",
}


async def get_profile(pool: asyncpg.Pool, user_id: int) -> asyncpg.Record:
    return await pool.fetchrow(
        """SELECT user_id, target_role, target_companies, resume_text,
                  goal_type, target_date, daily_time_budget_minutes, timezone
           FROM profiles WHERE user_id = $1""",
        user_id,
    )


async def update_profile(
    pool: asyncpg.Pool,
    user_id: int,
    target_role: str,
    target_companies: str,
    resume_text: str | None = None,
    goal_type: str = "",
    target_date: date | None = None,
    daily_time_budget_minutes: int | None = None,
    timezone: str = "",
) -> None:
    # resume_text keeps its existing None-means-"don't touch the stored
    # text" semantics (the file upload is optional on every save) -- the
    # newer goal fields are simple always-submitted form values instead,
    # so they don't need that same two-branch treatment.
    if resume_text is None:
        await pool.execute(
            """UPDATE profiles SET
                   target_role = $2, target_companies = $3,
                   goal_type = $4, target_date = $5, daily_time_budget_minutes = $6, timezone = $7,
                   updated_at = now()
               WHERE user_id = $1""",
            user_id,
            target_role,
            target_companies,
            goal_type,
            target_date,
            daily_time_budget_minutes,
            timezone,
        )
    else:
        await pool.execute(
            """UPDATE profiles SET
                   target_role = $2, target_companies = $3, resume_text = $4,
                   goal_type = $5, target_date = $6, daily_time_budget_minutes = $7, timezone = $8,
                   updated_at = now()
               WHERE user_id = $1""",
            user_id,
            target_role,
            target_companies,
            resume_text,
            goal_type,
            target_date,
            daily_time_budget_minutes,
            timezone,
        )


def extract_resume_text(filename: str, content: bytes) -> str:
    """Deterministic text extraction -- no LLM call needed for this step."""
    if filename.lower().endswith(".pdf"):
        from io import BytesIO

        from pypdf import PdfReader

        reader = PdfReader(BytesIO(content))
        return "\n".join(page.extract_text() or "" for page in reader.pages).strip()

    return content.decode("utf-8", errors="ignore").strip()
