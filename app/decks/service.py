from __future__ import annotations

import asyncpg

from app.core.moderation import check as moderate
from app.practice.service import create_question

_DECK_QUESTION_COLUMNS = (
    "question, answer, example, topic, difficulty, code_snippet, language, "
    "question_type, choices, correct_choice_index"
)


class DeckNotFound(Exception):
    pass


class EmptyTopic(Exception):
    """No questions in the publisher's own bank matched the chosen topic."""


class NameRejected(Exception):
    """A moderation check flagged the deck's name or description."""


async def publish_deck(
    pool: asyncpg.Pool, owner_user_id: int, *, name: str, description: str, topic: str
) -> int:
    """Snapshots every one of the publisher's own questions tagged with
    `topic` into a new public deck. A snapshot, not a live view -- see the
    migration's own comment for why: the owner editing or deleting their
    questions later must never change or break a deck someone already
    imported, and this must never expose more than the one topic chosen."""
    name = name.strip()
    if not name:
        raise NameRejected("Give the deck a name.")
    if (await moderate(name)).hard_block or (description and (await moderate(description)).hard_block):
        raise NameRejected("That deck's name or description was flagged. Try rewording it.")

    source_rows = await pool.fetch(
        "SELECT question, answer, example, topic, difficulty, code_snippet, language, "
        "question_type, choices, correct_choice_index "
        "FROM questions WHERE user_id = $1 AND topic = $2",
        owner_user_id,
        topic,
    )
    if not source_rows:
        raise EmptyTopic(topic)

    deck_id = await pool.fetchval(
        """INSERT INTO shared_decks (owner_user_id, name, description, topic, question_count)
           VALUES ($1, $2, $3, $4, $5) RETURNING deck_id""",
        owner_user_id,
        name,
        description.strip(),
        topic,
        len(source_rows),
    )
    for row in source_rows:
        await pool.execute(
            f"""INSERT INTO shared_deck_questions (deck_id, {_DECK_QUESTION_COLUMNS})
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)""",
            deck_id,
            row["question"],
            row["answer"],
            row["example"],
            row["topic"],
            row["difficulty"],
            row["code_snippet"],
            row["language"],
            row["question_type"],
            row["choices"],
            row["correct_choice_index"],
        )
    return deck_id


async def list_public_decks(pool: asyncpg.Pool, *, query: str = "") -> list[dict]:
    """Newest first -- no ranking algorithm to game with a tiny, brand-new
    gallery; import_count is shown per-deck instead, as a real signal
    once decks actually accumulate imports."""
    query = query.strip()
    if query:
        rows = await pool.fetch(
            """SELECT d.deck_id, d.name, d.description, d.topic, d.question_count,
                      d.import_count, d.created_at, u.name AS owner_name
               FROM shared_decks d JOIN users u ON u.user_id = d.owner_user_id
               WHERE d.name ILIKE $1 OR d.topic ILIKE $1 OR d.description ILIKE $1
               ORDER BY d.created_at DESC LIMIT 100""",
            f"%{query}%",
        )
    else:
        rows = await pool.fetch(
            """SELECT d.deck_id, d.name, d.description, d.topic, d.question_count,
                      d.import_count, d.created_at, u.name AS owner_name
               FROM shared_decks d JOIN users u ON u.user_id = d.owner_user_id
               ORDER BY d.created_at DESC LIMIT 100"""
        )
    return [dict(r) for r in rows]


async def list_my_decks(pool: asyncpg.Pool, owner_user_id: int) -> list[dict]:
    rows = await pool.fetch(
        """SELECT deck_id, name, description, topic, question_count, import_count, created_at
           FROM shared_decks WHERE owner_user_id = $1 ORDER BY created_at DESC""",
        owner_user_id,
    )
    return [dict(r) for r in rows]


async def get_deck_detail(pool: asyncpg.Pool, deck_id: int) -> dict:
    deck = await pool.fetchrow(
        """SELECT d.deck_id, d.owner_user_id, d.name, d.description, d.topic, d.question_count,
                  d.import_count, d.created_at, u.name AS owner_name
           FROM shared_decks d JOIN users u ON u.user_id = d.owner_user_id
           WHERE d.deck_id = $1""",
        deck_id,
    )
    if deck is None:
        raise DeckNotFound(deck_id)

    questions = await pool.fetch(
        "SELECT question, answer, question_type, choices FROM shared_deck_questions "
        "WHERE deck_id = $1 ORDER BY shared_deck_question_id",
        deck_id,
    )
    result = dict(deck)
    result["questions"] = [dict(q) for q in questions]
    return result


async def import_deck(pool: asyncpg.Pool, importer_user_id: int, deck_id: int) -> dict:
    """Copies the deck's snapshot rows into the importer's own bank via the
    same create_question() every other import path (CSV, .apkg) already
    uses -- same embedding, same source-tagging pattern. Dedupes against
    the importer's *own* existing bank, not the deck's original owner's."""
    rows = await pool.fetch(
        f"SELECT {_DECK_QUESTION_COLUMNS} FROM shared_deck_questions WHERE deck_id = $1 "
        "ORDER BY shared_deck_question_id",
        deck_id,
    )
    if not rows:
        raise DeckNotFound(deck_id)

    existing = {
        r["question"].casefold()
        for r in await pool.fetch("SELECT question FROM questions WHERE user_id = $1", importer_user_id)
    }
    added = 0
    for row in rows:
        if row["question"].casefold() in existing:
            continue
        await create_question(pool, importer_user_id, dict(row), source="shared_deck")
        existing.add(row["question"].casefold())
        added += 1

    await pool.execute("UPDATE shared_decks SET import_count = import_count + 1 WHERE deck_id = $1", deck_id)
    return {"added": added, "skipped": len(rows) - added}


async def delete_deck(pool: asyncpg.Pool, owner_user_id: int, deck_id: int) -> None:
    owned = await pool.fetchval(
        "SELECT deck_id FROM shared_decks WHERE deck_id = $1 AND owner_user_id = $2", deck_id, owner_user_id
    )
    if owned is None:
        raise DeckNotFound(deck_id)
    await pool.execute("DELETE FROM shared_decks WHERE deck_id = $1", deck_id)
