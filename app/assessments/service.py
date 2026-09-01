from __future__ import annotations

import json

import asyncpg

from app.core.json_extraction import extract_first_json_value
from app.core.llm import generate
from app.gamification.service import award_xp

# Fixed-length, not the plan's adaptive-difficulty-mid-quiz version --
# generated up front with a spread of difficulty (see the prompt below)
# rather than branching after each answer. Honest, bounded scope; true
# per-answer adaptivity is a real follow-up, not something to fake with
# a handful of if/else difficulty tiers.
QUESTION_COUNT = 8
_MAX_CHOICES = 6
_XP_DIAGNOSTIC = 20  # a flat completion reward -- this is a placement test, not a "get it right" quiz

_DIAGNOSTIC_PROMPT = """Write a {count}-question multiple-choice diagnostic quiz for this
topic: "{topic}"

Roughly a third of the questions should be easy, a third medium, a third hard. Each
question should test a different subtopic within "{topic}", so a wrong answer says
something specific about what the student doesn't know yet.

Output strict JSON only, no markdown fences, no commentary, in exactly this shape:
{{
  "questions": [
    {{
      "question": "...",
      "subtopic": "a short 2-4 word subtopic label",
      "choices": ["...", "...", "...", "..."],
      "correct_choice_index": 0
    }}
  ]
}}
"""


class DiagnosticUnavailable(Exception):
    """No LLM configured. Unlike a learning-path skeleton or lesson
    content, a diagnostic has no honest deterministic fallback -- there's
    no template for "generate real assessment questions on an arbitrary
    topic" that isn't just faking the assessment. Callers show a clear
    "needs an AI provider" message instead of a fake quiz."""


class DiagnosticGenerationFailed(Exception):
    """The LLM call happened but produced something unusable (bad JSON,
    zero valid questions after validation)."""


def _validate_questions(data: dict) -> list[dict]:
    if not isinstance(data, dict):
        raise ValueError("not an object")
    raw_questions = data.get("questions")
    if not isinstance(raw_questions, list) or not raw_questions:
        raise ValueError("no questions")

    cleaned = []
    for q in raw_questions[:QUESTION_COUNT]:
        if not isinstance(q, dict):
            continue
        question_text = str(q.get("question") or "").strip()
        choices_raw = q.get("choices")
        choices = [
            str(c).strip() for c in (choices_raw if isinstance(choices_raw, list) else []) if str(c).strip()
        ]
        choices = choices[:_MAX_CHOICES]
        correct_index = q.get("correct_choice_index")
        if not question_text or len(choices) < 2:
            continue
        if not isinstance(correct_index, int) or not (0 <= correct_index < len(choices)):
            continue
        cleaned.append(
            {
                "question": question_text,
                "subtopic": str(q.get("subtopic") or "").strip(),
                "choices": choices,
                "correct_choice_index": correct_index,
            }
        )

    if not cleaned:
        raise ValueError("no usable questions after validation")
    return cleaned


async def generate_diagnostic(topic: str, *, ai_available: bool) -> list[dict]:
    if not ai_available:
        raise DiagnosticUnavailable()

    try:
        prompt = _DIAGNOSTIC_PROMPT.format(topic=topic.strip(), count=QUESTION_COUNT)
        response = await generate(prompt, temperature=0.5)
        data = json.loads(extract_first_json_value(response))
        return _validate_questions(data)
    except DiagnosticUnavailable:
        raise
    except Exception as exc:
        raise DiagnosticGenerationFailed() from exc


# (score-fraction floor, resulting level) -- checked high to low, first
# match wins. Reuses the exact level keys profile/service.py's own
# PROFICIENCY_LABELS already defines, so a diagnostic result and a
# self-reported one render through the same labels.
_LEVEL_THRESHOLDS = [
    (0.85, "advanced"),
    (0.65, "intermediate"),
    (0.35, "some_experience"),
    (0.0, "beginner"),
]


def score_to_level(correct_count: int, total_count: int) -> str:
    if total_count <= 0:
        return "beginner"
    fraction = correct_count / total_count
    for floor, level in _LEVEL_THRESHOLDS:
        if fraction >= floor:
            return level
    return "beginner"  # unreachable (0.0 always matches), kept for clarity


async def record_attempt(
    pool: asyncpg.Pool,
    user_id: int,
    topic: str,
    correct_count: int,
    total_count: int,
    weak_subtopics: list[str],
) -> dict:
    """Persists the result and, in the same transaction, updates the
    profile's proficiency to this *measured* value -- marked
    'diagnostic' so the profile/dashboard can show it as measured, not
    the Phase 2.1 self-reported dropdown value it may be overwriting."""
    level = score_to_level(correct_count, total_count)

    async with pool.acquire() as conn:
        async with conn.transaction():
            attempt_id = await conn.fetchval(
                """INSERT INTO diagnostic_attempts
                       (user_id, topic, correct_count, total_count, proficiency_result, weak_subtopics)
                   VALUES ($1, $2, $3, $4, $5, $6)
                   RETURNING attempt_id""",
                user_id,
                topic,
                correct_count,
                total_count,
                level,
                weak_subtopics,
            )
            await conn.execute(
                """UPDATE profiles SET
                       proficiency_level = $2, proficiency_source = 'diagnostic', updated_at = now()
                   WHERE user_id = $1""",
                user_id,
                level,
            )
            # award_xp takes anything with asyncpg's execute() -- a
            # Connection works the same as a Pool here, and doing it
            # inside this same transaction means the XP and the result
            # it's for are always persisted together.
            await award_xp(conn, user_id, "diagnostic", attempt_id, _XP_DIAGNOSTIC)

    return {"attempt_id": attempt_id, "proficiency_result": level}


async def list_attempts(pool: asyncpg.Pool, user_id: int) -> list[asyncpg.Record]:
    return await pool.fetch(
        """SELECT attempt_id, topic, correct_count, total_count,
                  proficiency_result, weak_subtopics, created_at
           FROM diagnostic_attempts WHERE user_id = $1 ORDER BY created_at DESC""",
        user_id,
    )


async def get_attempt(pool: asyncpg.Pool, user_id: int, attempt_id: int) -> asyncpg.Record | None:
    return await pool.fetchrow(
        """SELECT attempt_id, topic, correct_count, total_count,
                  proficiency_result, weak_subtopics, created_at
           FROM diagnostic_attempts WHERE attempt_id = $1 AND user_id = $2""",
        attempt_id,
        user_id,
    )
