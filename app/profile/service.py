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
                  day_rollover_hour, digest_opt_out, leaderboard_opt_out, github_url,
                  linkedin_url, website_url, onboarding_completed, proficiency_level,
                  proficiency_source
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


async def set_digest_opt_out(pool: asyncpg.Pool, user_id: int, opted_out: bool) -> None:
    """Whether the re-engagement digest skips this user. Its own function
    (not another update_profile param) because the one-click unsubscribe
    link sets it too, from outside the profile form."""
    await pool.execute("UPDATE profiles SET digest_opt_out = $2 WHERE user_id = $1", user_id, opted_out)


async def set_leaderboard_opt_out(pool: asyncpg.Pool, user_id: int, opted_out: bool) -> None:
    """Whether this user is excluded from every classroom leaderboard
    they're a member of. Opting out removes the row entirely rather than
    anonymizing it -- a student who doesn't want to be ranked in front of
    classmates shouldn't have to wonder if "Someone -- 340 XP" is them."""
    await pool.execute("UPDATE profiles SET leaderboard_opt_out = $2 WHERE user_id = $1", user_id, opted_out)


async def set_profile_links(
    pool: asyncpg.Pool, user_id: int, *, github: str, linkedin: str, website: str
) -> None:
    """The portfolio's contact links. Values are expected to be already
    cleaned (app.core.links.clean_url) -- empty string means "no link"."""
    await pool.execute(
        "UPDATE profiles SET github_url = $2, linkedin_url = $3, website_url = $4 WHERE user_id = $1",
        user_id,
        github,
        linkedin,
        website,
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
    proficiency_level: str = "",
    day_rollover_hour: int = 0,
) -> None:
    # resume_text keeps its existing None-means-"don't touch the stored
    # text" semantics (the file upload is optional on every save) -- every
    # other field is a simple always-submitted form value instead, so it
    # doesn't need that same two-branch treatment.
    #
    # proficiency_source is always reset to 'self_reported' here -- this
    # function is only ever called from the profile form's own dropdown,
    # never from the Phase 9 diagnostic (which sets it to 'diagnostic'
    # directly). Without this, saving the profile after taking a
    # diagnostic would leave a self-reported value mislabeled as measured.
    if resume_text is None:
        await pool.execute(
            """UPDATE profiles SET
                   target_role = $2, target_companies = $3,
                   goal_type = $4, target_date = $5, daily_time_budget_minutes = $6, timezone = $7,
                   proficiency_level = $8, day_rollover_hour = $9,
                   proficiency_source = 'self_reported', updated_at = now()
               WHERE user_id = $1""",
            user_id,
            target_role,
            target_companies,
            goal_type,
            target_date,
            daily_time_budget_minutes,
            timezone,
            proficiency_level,
            day_rollover_hour,
        )
    else:
        await pool.execute(
            """UPDATE profiles SET
                   target_role = $2, target_companies = $3, resume_text = $4,
                   goal_type = $5, target_date = $6, daily_time_budget_minutes = $7, timezone = $8,
                   proficiency_level = $9, day_rollover_hour = $10,
                   proficiency_source = 'self_reported', updated_at = now()
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
            day_rollover_hour,
        )


class InvalidUpload(Exception):
    pass


# Magic-byte signatures, not the filename's own extension -- the
# extension is whatever the uploader claims it is, unverified. PDF and
# DOCX both have a real, checkable file signature; DOCX (and every other
# Office Open XML format) is a zip archive under the hood, so its
# signature is the same as any zip's. Plain text formats (.txt/.md/.csv)
# have no reliable magic bytes -- the best available check there is
# "does this look like text at all," not "is this specifically a CSV."
_MAGIC_BYTES = {".pdf": b"%PDF-", ".docx": b"PK\x03\x04"}
_TEXT_EXTENSIONS = {".txt", ".md", ".csv"}
_SNIFF_LENGTH = 8192  # only the first few KB need checking for a NUL byte


def validate_upload_content(filename: str, content: bytes) -> None:
    """Rejects a file whose actual bytes don't match what its extension
    claims -- someone renaming an arbitrary binary to resume.pdf (or
    resume.txt) shouldn't have it silently accepted and stored just
    because the filename looked right. Not malware scanning (that needs
    a real AV engine/API this deploy doesn't have) -- this only catches
    "the content isn't even the claimed file type," which needs no
    external service at all."""
    lower_name = filename.lower()
    for ext, signature in _MAGIC_BYTES.items():
        if lower_name.endswith(ext) and not content.startswith(signature):
            raise InvalidUpload(f"That file doesn't look like a real {ext.lstrip('.').upper()} file.")

    if any(lower_name.endswith(ext) for ext in _TEXT_EXTENSIONS) and b"\x00" in content[:_SNIFF_LENGTH]:
        raise InvalidUpload("That file doesn't look like plain text.")


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
