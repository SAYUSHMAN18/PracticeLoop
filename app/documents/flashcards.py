from __future__ import annotations

import json

import asyncpg

from app.core.json_extraction import extract_first_json_value
from app.core.llm import generate
from app.core.logging import get_logger
from app.practice.service import create_question

logger = get_logger(__name__)

# Keeps the prompt bounded regardless of how long the source document is --
# a few thousand characters is plenty of material for a handful of cards,
# and an unbounded prompt risks both cost and the model losing focus.
_MAX_SOURCE_CHARS = 6000
_DEFAULT_COUNT = 5

_FLASHCARD_PROMPT = """Generate {count} study flashcards (question + answer pairs) from
the material below. Base every question and answer ONLY on this material -- never invent
a fact that isn't actually in it.

MATERIAL:
{source}

Output a strict JSON array of exactly {count} objects, each with keys "question" and
"answer". Output ONLY the JSON array, no markdown fences, no commentary.
"""


async def _fallback_single_card(pool: asyncpg.Pool, user_id: int, source_text: str, topic: str) -> list[int]:
    excerpt = source_text[:800].strip()
    if len(source_text) > 800:
        excerpt += "..."
    question_id = await create_question(
        pool,
        user_id,
        {
            "question": f'Summarize the key points of "{topic}" in your own words.',
            "answer": excerpt,
            "topic": topic,
        },
        source="ai_generated",
    )
    return [question_id]


async def generate_flashcards_from_text(
    pool: asyncpg.Pool,
    user_id: int,
    source_text: str,
    topic: str,
    *,
    ai_available: bool,
    count: int = _DEFAULT_COUNT,
) -> list[int]:
    """Phase 4.4's flashcard generator, applied to a document already in
    the vault -- closes the loop between "upload content" and "practice
    from it" instead of leaving an uploaded file just sitting there.

    No deterministic fallback attempts real flashcard generation without
    an LLM -- unlike, say, resume-tailoring's keyword-overlap diff, there's
    no safe template for "invent a good question from arbitrary text."
    The fallback is one honest, clearly-labeled card pointing back at the
    source excerpt instead -- not a full feature, but not a dead end
    either, consistent with every other LLM-backed feature in this app."""
    if not source_text.strip():
        return []

    if not ai_available:
        return await _fallback_single_card(pool, user_id, source_text, topic)

    prompt = _FLASHCARD_PROMPT.format(count=count, source=source_text[:_MAX_SOURCE_CHARS])

    try:
        response = await generate(prompt, temperature=0.4)
        cards = json.loads(extract_first_json_value(response))
        if not isinstance(cards, list):
            raise ValueError("Expected a JSON array")
    except Exception:
        logger.warning("Flashcard generation failed, falling back to a single summary card", exc_info=True)
        return await _fallback_single_card(pool, user_id, source_text, topic)

    question_ids = []
    for card in cards:
        if not isinstance(card, dict):
            continue
        question = str(card.get("question") or "").strip()
        answer = str(card.get("answer") or "").strip()
        if not question:
            continue
        question_id = await create_question(
            pool, user_id, {"question": question, "answer": answer, "topic": topic}, source="ai_generated"
        )
        question_ids.append(question_id)

    # The model returned valid JSON but somehow no usable cards -- still
    # not a dead end.
    if not question_ids:
        return await _fallback_single_card(pool, user_id, source_text, topic)

    return question_ids
