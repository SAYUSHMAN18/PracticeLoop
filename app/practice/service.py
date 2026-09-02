from __future__ import annotations

from datetime import date, timedelta

import asyncpg

from app.core.embedder import embed_text_async
from app.core.llm import generate
from app.gamification.service import award_xp
from app.practice.extraction import parse_llm_json_fields
from app.practice.fsrs_scheduler import schedule_review

_QUESTION_COLUMNS = (
    "question_id, user_id, question, answer, example, topic, difficulty, "
    "company, code_snippet, language, source, created_at, "
    "question_type, choices, correct_choice_index, source_lesson_id"
)

# Cosine distance cutoff for "this counts as a match" -- pgvector's <=> ranges
# 0 (identical) to 2 (opposite) for normalized vectors; empirically, unrelated
# sentence-transformer embeddings for short questions sit well above this.
_SEARCH_DISTANCE_THRESHOLD = 0.65


class QuestionNotFound(Exception):
    pass


_VALID_DIFFICULTIES = {"easy", "medium", "hard"}


def _normalize_difficulty(value: str) -> str:
    """The manual-entry form is a <select> constrained to the three valid
    values, but AI-structured and marker-parsed text isn't -- fall back to
    'medium' rather than let a stray LLM/free-text value hit the DB's
    difficulty CHECK constraint as an unhandled 500."""
    candidate = (value or "").strip().lower()
    return candidate if candidate in _VALID_DIFFICULTIES else "medium"


_VALID_QUESTION_TYPES = {"free_text", "multiple_choice"}


async def create_question(pool: asyncpg.Pool, user_id: int, fields: dict, source: str = "manual") -> int:
    """`fields["question_type"]` defaults to the original free-text type,
    so every existing call site (manual capture, AI structuring, marker
    parsing, flashcard/study-card generation) is unaffected -- only a
    caller that explicitly asks for "multiple_choice" (Phase 9's
    diagnostic, Phase 10's quiz modes) needs to pass `choices` and
    `correct_choice_index` too."""
    embedding_source = f"{fields['question']}\n{fields.get('topic', '')}".strip()
    embedding = await embed_text_async(embedding_source)

    question_type = fields.get("question_type", "free_text")
    if question_type not in _VALID_QUESTION_TYPES:
        question_type = "free_text"
    choices = fields.get("choices") if question_type == "multiple_choice" else None
    correct_choice_index = fields.get("correct_choice_index") if question_type == "multiple_choice" else None

    return await pool.fetchval(
        """INSERT INTO questions
                (user_id, question, answer, example, topic, difficulty,
                 company, code_snippet, language, source, embedding,
                 question_type, choices, correct_choice_index)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14)
            RETURNING question_id""",
        user_id,
        fields["question"],
        fields.get("answer", ""),
        fields.get("example", ""),
        fields.get("topic", ""),
        _normalize_difficulty(fields.get("difficulty", "medium")),
        fields.get("company", ""),
        fields.get("code_snippet", ""),
        fields.get("language", ""),
        source,
        embedding,
        question_type,
        choices,
        correct_choice_index,
    )


async def update_question(pool: asyncpg.Pool, user_id: int, question_id: int, fields: dict) -> None:
    owned = await get_question(pool, user_id, question_id)
    if owned is None:
        raise QuestionNotFound(question_id)

    embedding_source = f"{fields['question']}\n{fields.get('topic', '')}".strip()
    embedding = await embed_text_async(embedding_source)

    await pool.execute(
        """UPDATE questions SET
               question = $3, answer = $4, example = $5, topic = $6, difficulty = $7,
               company = $8, code_snippet = $9, language = $10, embedding = $11
           WHERE question_id = $1 AND user_id = $2""",
        question_id,
        user_id,
        fields["question"],
        fields.get("answer", ""),
        fields.get("example", ""),
        fields.get("topic", ""),
        _normalize_difficulty(fields.get("difficulty", "medium")),
        fields.get("company", ""),
        fields.get("code_snippet", ""),
        fields.get("language", ""),
        embedding,
    )


async def delete_question(pool: asyncpg.Pool, user_id: int, question_id: int) -> None:
    owned = await get_question(pool, user_id, question_id)
    if owned is None:
        raise QuestionNotFound(question_id)

    await pool.execute("DELETE FROM questions WHERE question_id = $1 AND user_id = $2", question_id, user_id)


async def list_questions(pool: asyncpg.Pool, user_id: int) -> list[asyncpg.Record]:
    return await pool.fetch(
        f"SELECT {_QUESTION_COLUMNS} FROM questions WHERE user_id = $1 ORDER BY created_at DESC",
        user_id,
    )


async def list_topics(pool: asyncpg.Pool, user_id: int) -> list[str]:
    """Distinct, non-blank topics in this user's own bank -- populates
    Quiz Arena's topic picker without a separate topics table."""
    rows = await pool.fetch(
        "SELECT DISTINCT topic FROM questions WHERE user_id = $1 AND topic != '' ORDER BY topic",
        user_id,
    )
    return [r["topic"] for r in rows]


async def get_quiz_questions(
    pool: asyncpg.Pool, user_id: int, *, topic: str | None, count: int
) -> list[asyncpg.Record]:
    """Quiz Arena's question pool -- unlike the FSRS review queue, this
    draws from the *whole* bank regardless of due date (a deliberate
    replay-anything mode, not spaced review), random order, optionally
    narrowed to one topic."""
    if topic:
        return await pool.fetch(
            f"""SELECT {_QUESTION_COLUMNS} FROM questions
                WHERE user_id = $1 AND topic = $2
                ORDER BY random() LIMIT $3""",
            user_id,
            topic,
            count,
        )
    return await pool.fetch(
        f"SELECT {_QUESTION_COLUMNS} FROM questions WHERE user_id = $1 ORDER BY random() LIMIT $2",
        user_id,
        count,
    )


async def get_question(pool: asyncpg.Pool, user_id: int, question_id: int) -> asyncpg.Record | None:
    return await pool.fetchrow(
        f"SELECT {_QUESTION_COLUMNS} FROM questions WHERE user_id = $1 AND question_id = $2",
        user_id,
        question_id,
    )


async def search_questions(
    pool: asyncpg.Pool, user_id: int, query_text: str, top_k: int = 5
) -> list[asyncpg.Record]:
    embedding = await embed_text_async(query_text)
    rows = await pool.fetch(
        f"""SELECT {_QUESTION_COLUMNS}, embedding <=> $1 AS distance
            FROM questions
            WHERE user_id = $2 AND embedding <=> $1 < $4
            ORDER BY embedding <=> $1
            LIMIT $3""",
        embedding,
        user_id,
        top_k,
        _SEARCH_DISTANCE_THRESHOLD,
    )
    return [{**dict(r), "match_pct": max(0, round((1 - r["distance"]) * 100))} for r in rows]


_XP_STRONG_RECALL = 10  # rating 4-5 ("good"/"easy")
_XP_OKAY_RECALL = 6  # rating 3 ("good enough")
_XP_WEAK_RECALL = 3  # rating 1-2 -- still a real attempt, just a smaller reward than a solid recall


def _attempt_xp(rating: int) -> int:
    if rating >= 4:
        return _XP_STRONG_RECALL
    if rating == 3:
        return _XP_OKAY_RECALL
    return _XP_WEAK_RECALL


async def record_attempt(
    pool: asyncpg.Pool, user_id: int, question_id: int, rating: int, feedback: str = ""
) -> date:
    owned = await get_question(pool, user_id, question_id)
    if owned is None:
        raise QuestionNotFound(question_id)

    review_date = await schedule_review(pool, user_id, question_id, rating)

    attempt_id = await pool.fetchval(
        """INSERT INTO attempts (question_id, user_id, confidence_rating, feedback, next_review_at)
           VALUES ($1, $2, $3, $4, $5)
           RETURNING attempt_id""",
        question_id,
        user_id,
        rating,
        feedback,
        review_date,
    )
    # A fresh attempt always gets a fresh attempt_id, so award_xp's own
    # (user_id, source_type, source_id) uniqueness never dedupes real,
    # repeated practice -- only a genuinely duplicate event (a retried
    # request re-inserting the same row) could ever collide, and that
    # can't happen here since each INSERT makes a new id.
    await award_xp(pool, user_id, "practice_attempt", attempt_id, _attempt_xp(rating))
    return review_date


# Deterministic rating for an auto-graded multiple-choice answer -- 4
# ("Good") for correct rather than 5 ("Easy"), 2 ("Hard") for incorrect
# rather than 1 ("Again"/blackout): a right or wrong MCQ pick doesn't
# carry the same certainty as a self-assessed "I knew this cold" or "I
# drew a total blank," so it lands one notch inside those extremes.
_MCQ_CORRECT_RATING = 4
_MCQ_INCORRECT_RATING = 2


async def record_mcq_attempt(
    pool: asyncpg.Pool, user_id: int, question_id: int, selected_index: int
) -> tuple[date, bool]:
    """Grades a multiple-choice answer deterministically (no LLM call --
    the whole point of this question type) and records it through the
    same FSRS scheduling path free-text answers use, so a mixed deck of
    both types still gets one coherent review queue instead of two."""
    question = await get_question(pool, user_id, question_id)
    if question is None or question["question_type"] != "multiple_choice":
        raise QuestionNotFound(question_id)

    is_correct = selected_index == question["correct_choice_index"]
    rating = _MCQ_CORRECT_RATING if is_correct else _MCQ_INCORRECT_RATING
    review_date = await record_attempt(pool, user_id, question_id, rating)
    return review_date, is_correct


async def due_for_review(pool: asyncpg.Pool, user_id: int, today: date | None = None) -> list[asyncpg.Record]:
    today = today or date.today()
    return await pool.fetch(
        f"""SELECT {", ".join("q." + c for c in _QUESTION_COLUMNS.split(", "))}, cs.due AS next_review_at
            FROM questions q
            LEFT JOIN card_states cs ON cs.question_id = q.question_id
            WHERE q.user_id = $1 AND (cs.due IS NULL OR cs.due::date <= $2)
            ORDER BY cs.due NULLS FIRST, q.created_at""",
        user_id,
        today,
    )


async def build_daily_plan(pool: asyncpg.Pool, user_id: int) -> list[dict]:
    """Phase 3.2's adaptive daily session: everything actually due, plus up
    to one pick each from the weakest topic and from hard-difficulty
    questions -- each labeled with why it's there, each deduplicated
    against what's already in the list.

    A never-attempted question is *already* correctly included in "due"
    (a fresh card is due immediately -- due_for_review's cs.due IS NULL
    branch), so there's no separate, disjoint pool of "new" questions to
    query for: every never-attempted question is, by construction, always
    already in this list. Querying for "never attempted AND not already
    in the list" would always return nothing once at least one exists,
    and nothing at all otherwise -- dead code either way. Instead, the
    single oldest never-attempted item already in `due` (due_for_review's
    own NULLS-FIRST, oldest-first ordering picks it out for us) is
    relabeled "new" in place, matching what the dashboard's own
    new-concept card already surfaces.

    Both bonus picks are oldest-first, not random -- deterministic beats
    random for anything a test (or a confused user comparing two page
    loads) needs to reason about. Not persisted -- built fresh on every
    call, so it always reflects current state. Session-scoped state for
    "which of today's picks has the user already gotten to" lives in the
    /practice/plan routes, not here; this function only ever answers
    "what would today's plan be right now.\""""
    due = await due_for_review(pool, user_id)
    seen_ids = [q["question_id"] for q in due]

    plan = []
    labeled_new = False
    for q in due:
        if q["next_review_at"] is None and not labeled_new:
            plan.append({"question": q, "reason": "new"})
            labeled_new = True
        else:
            plan.append({"question": q, "reason": "due"})

    weakest_topic = await pool.fetchval(
        """SELECT q.topic FROM questions q JOIN attempts a ON a.question_id = q.question_id
           WHERE q.user_id = $1 AND q.topic != ''
           GROUP BY q.topic ORDER BY avg(a.confidence_rating) ASC LIMIT 1""",
        user_id,
    )
    if weakest_topic:
        weak_pick = await pool.fetchrow(
            f"""SELECT {_QUESTION_COLUMNS} FROM questions
                WHERE user_id = $1 AND topic = $2 AND question_id != ALL($3::int[])
                ORDER BY created_at ASC LIMIT 1""",
            user_id,
            weakest_topic,
            seen_ids,
        )
        if weak_pick:
            plan.append({"question": weak_pick, "reason": "weak"})
            seen_ids.append(weak_pick["question_id"])

    challenge_pick = await pool.fetchrow(
        f"""SELECT {_QUESTION_COLUMNS} FROM questions
            WHERE user_id = $1 AND difficulty = 'hard' AND question_id != ALL($2::int[])
            ORDER BY created_at ASC LIMIT 1""",
        user_id,
        seen_ids,
    )
    if challenge_pick:
        plan.append({"question": challenge_pick, "reason": "challenge"})

    return plan


async def study_history(pool: asyncpg.Pool, user_id: int, limit: int = 200) -> list[asyncpg.Record]:
    """Phase 3.3, narrowed to what this app can support without the
    notification/calendar infrastructure (Phase 13) the plan's fuller
    calendar view depends on: a plain reverse-chronological log of real
    review activity, grouped by day in the template. Every attempt is
    already recorded in the attempts table -- this is a read, not new
    data collection."""
    return await pool.fetch(
        """SELECT a.attempt_id, a.practiced_at, a.confidence_rating, a.feedback,
                  q.question, q.topic, q.question_id
           FROM attempts a JOIN questions q ON q.question_id = a.question_id
           WHERE a.user_id = $1
           ORDER BY a.practiced_at DESC
           LIMIT $2""",
        user_id,
        limit,
    )


async def get_questions_by_ids(
    pool: asyncpg.Pool, user_id: int, question_ids: list[int]
) -> list[asyncpg.Record]:
    """Fetches an ownership-checked set of questions in the exact order
    given -- used to replay a session-stored daily plan, where the order
    (due first, then weak/new/challenge) is the point, not just "some
    order the DB feels like.\""""
    if not question_ids:
        return []
    return await pool.fetch(
        f"""SELECT {_QUESTION_COLUMNS} FROM questions
            WHERE user_id = $1 AND question_id = ANY($2::int[])
            ORDER BY array_position($2::int[], question_id)""",
        user_id,
        question_ids,
    )


async def streak_days(pool: asyncpg.Pool, user_id: int) -> int:
    rows = await pool.fetch(
        "SELECT DISTINCT practiced_at::date AS d FROM attempts WHERE user_id = $1 ORDER BY d DESC",
        user_id,
    )
    dates = [r["d"] for r in rows]
    if not dates:
        return 0

    today = date.today()
    if dates[0] == today:
        expected = today - timedelta(days=1)
    elif dates[0] == today - timedelta(days=1):
        expected = today - timedelta(days=2)
    else:
        return 0

    streak = 1
    for d in dates[1:]:
        if d == expected:
            streak += 1
            expected -= timedelta(days=1)
        else:
            break
    return streak


async def seed_starter_deck(pool: asyncpg.Pool, user_id: int) -> int:
    """Load data/starter_deck.json for a new user so the review queue and
    search corpus have real content on day one instead of four zeros."""
    import json
    from pathlib import Path

    deck_path = Path(__file__).resolve().parents[2] / "data" / "starter_deck.json"
    entries = json.loads(deck_path.read_text(encoding="utf-8"))

    for entry in entries:
        await create_question(pool, user_id, entry, source="starter_deck")

    return len(entries)


_GENERATE_PROMPT = """Generate one interview/practice question about "{topic}" at
{difficulty} difficulty. It must be different from these existing questions:
{existing}

Output strict JSON with exactly these keys: question, answer, example, topic, company,
difficulty, code_snippet, language. Use "" for any field that doesn't apply. Output ONLY
the JSON object, no markdown fences, no explanation.
"""


async def generate_study_card(
    pool: asyncpg.Pool, user_id: int, topic: str, difficulty: str = "medium"
) -> int:
    existing_rows = await pool.fetch(
        "SELECT question FROM questions WHERE user_id = $1 AND topic ILIKE $2 LIMIT 20",
        user_id,
        f"%{topic}%",
    )
    existing_text = "\n".join(f"- {r['question']}" for r in existing_rows) or "(none yet)"
    prompt = _GENERATE_PROMPT.format(topic=topic, difficulty=difficulty, existing=existing_text)

    try:
        response = await generate(prompt, temperature=0.7)
        fields = parse_llm_json_fields(response)
    except (ValueError, LookupError):
        # One retry with temperature 0 and a stricter instruction -- most
        # malformed-JSON responses are a one-off formatting slip, not a
        # persistent failure.
        strict_prompt = prompt + "\nReturn ONLY the raw JSON object. No prose, no fences."
        response = await generate(strict_prompt, temperature=0.0)
        fields = parse_llm_json_fields(response)

    fields.setdefault("topic", topic)
    fields.setdefault("difficulty", difficulty)

    return await create_question(pool, user_id, fields, source="ai_generated")
