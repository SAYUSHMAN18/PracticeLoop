from __future__ import annotations

from datetime import date, timedelta

import asyncpg

from app.core.embedder import embed_text
from app.core.llm import generate
from app.practice.extraction import parse_llm_json_fields
from app.practice.spaced_repetition import next_review_date

_QUESTION_COLUMNS = (
    "question_id, user_id, question, answer, example, topic, difficulty, "
    "company, code_snippet, language, source, created_at"
)


async def create_question(pool: asyncpg.Pool, user_id: int, fields: dict, source: str = "manual") -> int:
    embedding_source = f"{fields['question']}\n{fields.get('topic', '')}".strip()
    embedding = embed_text(embedding_source)

    return await pool.fetchval(
        f"""INSERT INTO questions
                (user_id, question, answer, example, topic, difficulty,
                 company, code_snippet, language, source, embedding)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
            RETURNING question_id""",
        user_id,
        fields["question"],
        fields.get("answer", ""),
        fields.get("example", ""),
        fields.get("topic", ""),
        fields.get("difficulty", "medium"),
        fields.get("company", ""),
        fields.get("code_snippet", ""),
        fields.get("language", ""),
        source,
        embedding,
    )


async def list_questions(pool: asyncpg.Pool, user_id: int) -> list[asyncpg.Record]:
    return await pool.fetch(
        f"SELECT {_QUESTION_COLUMNS} FROM questions WHERE user_id = $1 ORDER BY created_at DESC",
        user_id,
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
    embedding = embed_text(query_text)
    return await pool.fetch(
        f"""SELECT {_QUESTION_COLUMNS}, embedding <=> $1 AS distance
            FROM questions
            WHERE user_id = $2
            ORDER BY embedding <=> $1
            LIMIT $3""",
        embedding,
        user_id,
        top_k,
    )


async def _previous_interval_days(pool: asyncpg.Pool, question_id: int) -> int | None:
    row = await pool.fetchrow(
        """SELECT practiced_at, next_review_at FROM attempts
           WHERE question_id = $1 ORDER BY practiced_at DESC LIMIT 1""",
        question_id,
    )
    if row is None:
        return None
    return (row["next_review_at"] - row["practiced_at"].date()).days


async def record_attempt(
    pool: asyncpg.Pool, user_id: int, question_id: int, rating: int, feedback: str = ""
) -> date:
    previous_interval = await _previous_interval_days(pool, question_id)
    review_date, _interval = next_review_date(rating, previous_interval)

    await pool.execute(
        """INSERT INTO attempts (question_id, user_id, confidence_rating, feedback, next_review_at)
           VALUES ($1, $2, $3, $4, $5)""",
        question_id,
        user_id,
        rating,
        feedback,
        review_date,
    )
    return review_date


async def due_for_review(pool: asyncpg.Pool, user_id: int, today: date | None = None) -> list[asyncpg.Record]:
    today = today or date.today()
    return await pool.fetch(
        f"""SELECT {", ".join("q." + c for c in _QUESTION_COLUMNS.split(", "))}, latest.next_review_at
            FROM questions q
            LEFT JOIN LATERAL (
                SELECT next_review_at FROM attempts a
                WHERE a.question_id = q.question_id
                ORDER BY practiced_at DESC LIMIT 1
            ) latest ON true
            WHERE q.user_id = $1 AND (latest.next_review_at IS NULL OR latest.next_review_at <= $2)
            ORDER BY latest.next_review_at NULLS FIRST, q.created_at""",
        user_id,
        today,
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
    response = await generate(prompt, temperature=0.7)
    fields = parse_llm_json_fields(response)
    fields.setdefault("topic", topic)
    fields.setdefault("difficulty", difficulty)

    return await create_question(pool, user_id, fields, source="ai_generated")
