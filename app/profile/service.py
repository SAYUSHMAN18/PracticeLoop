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

# Phase 2.1 step 6 -- a self-reported starting point, not the full adaptive
# diagnostic (Phase 5.2, a real assessment feature, not a form field).
PROFICIENCY_LABELS = {
    "": "Not set",
    "beginner": "Just getting started",
    "some_experience": "Some experience",
    "intermediate": "Comfortable with the basics",
    "advanced": "Advanced -- refining and filling gaps",
}


async def get_profile(pool: asyncpg.Pool, user_id: int) -> asyncpg.Record:
    return await pool.fetchrow(
        """SELECT user_id, target_role, target_companies, resume_text,
                  goal_type, target_date, daily_time_budget_minutes, timezone,
                  onboarding_completed, proficiency_level
           FROM profiles WHERE user_id = $1""",
        user_id,
    )


async def needs_onboarding(pool: asyncpg.Pool, user_id: int) -> bool:
    """True for anyone -- brand new or pre-existing -- who's never seen the
    one-time goal-setting welcome screen. Set-once via mark_onboarded, not
    re-derived from whether goal fields are empty, so an explicit "skip"
    actually sticks instead of re-prompting on every login."""
    completed = await pool.fetchval("SELECT onboarding_completed FROM profiles WHERE user_id = $1", user_id)
    return not completed


async def mark_onboarded(pool: asyncpg.Pool, user_id: int) -> None:
    await pool.execute("UPDATE profiles SET onboarding_completed = true WHERE user_id = $1", user_id)


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
    proficiency_level: str = "",
) -> None:
    # resume_text keeps its existing None-means-"don't touch the stored
    # text" semantics (the file upload is optional on every save) -- every
    # other field is a simple always-submitted form value instead, so it
    # doesn't need that same two-branch treatment.
    if resume_text is None:
        await pool.execute(
            """UPDATE profiles SET
                   target_role = $2, target_companies = $3,
                   goal_type = $4, target_date = $5, daily_time_budget_minutes = $6, timezone = $7,
                   proficiency_level = $8,
                   updated_at = now()
               WHERE user_id = $1""",
            user_id,
            target_role,
            target_companies,
            goal_type,
            target_date,
            daily_time_budget_minutes,
            timezone,
            proficiency_level,
        )
    else:
        await pool.execute(
            """UPDATE profiles SET
                   target_role = $2, target_companies = $3, resume_text = $4,
                   goal_type = $5, target_date = $6, daily_time_budget_minutes = $7, timezone = $8,
                   proficiency_level = $9,
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
            proficiency_level,
        )


def extract_text_from_file(filename: str, content: bytes) -> str:
    """Deterministic text extraction -- no LLM call needed for this step.
    Phase 4.1's supported-sources list also asks for images/OCR and audio/
    video transcription; those need a real binary (Tesseract) or a
    transcription service this deploy doesn't have configured, so they're
    deliberately not here -- PDF, DOCX, and plain text/Markdown/CSV (which
    are all just text) cover what's realistically extractable with pure
    Python and no new infrastructure. Shared by both the profile resume
    upload and the document vault -- one extractor, not two copies."""
    lower_name = filename.lower()
    from io import BytesIO

    if lower_name.endswith(".pdf"):
        from pypdf import PdfReader

        reader = PdfReader(BytesIO(content))
        return "\n".join(page.extract_text() or "" for page in reader.pages).strip()

    if lower_name.endswith(".docx"):
        from docx import Document

        doc = Document(BytesIO(content))
        return "\n".join(p.text for p in doc.paragraphs).strip()

    # .txt, .md, .csv, and anything else are just plain text.
    return content.decode("utf-8", errors="ignore").strip()
