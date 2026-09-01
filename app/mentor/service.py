from __future__ import annotations

import asyncpg

from app.core.llm import generate
from app.core.llm_budget import consume_llm_budget
from app.core.logging import get_logger

logger = get_logger(__name__)

VALID_CONTEXT_TYPES = {"general", "lesson", "path"}

# Quick actions (Phase 11's "Quick actions" panel) -- each is just a
# canned user message, sent through the exact same send_message() path
# as anything typed by hand. Not a separate mode system; the context
# already attached to the conversation is what makes the reply relevant.
QUICK_ACTIONS = {
    "explain_simply": "Explain what we just covered more simply, like I'm new to this.",
    "give_hint": "Give me a hint for the current checkpoint question, without giving away the full answer.",
    "memory_trick": "Give me a memory trick (a mnemonic or analogy) to remember this.",
    "study_next": "Based on my weakest topics, what should I study next and why?",
}

_MAX_HISTORY_MESSAGES = 10

# Canned, non-AI-generated replies -- exported so the router/template can
# skip the "AI-generated" disclaimer on these specifically (a hardcoded
# "no provider configured" string labeled "AI-generated" would itself be
# a small dishonesty, the opposite of what that disclaimer is for).
_REPLY_NEEDS_AI_PROVIDER = (
    "Loop Mentor needs an AI provider configured to reply -- ask your admin to set one up."
)
_REPLY_BUDGET_EXHAUSTED = (
    "You've used all your AI generations for today -- Loop Mentor will be back tomorrow."
)
_REPLY_GENERATION_FAILED = "Sorry, I couldn't generate a reply just now -- try again in a moment."
CANNED_REPLIES = {_REPLY_NEEDS_AI_PROVIDER, _REPLY_BUDGET_EXHAUSTED, _REPLY_GENERATION_FAILED}

_SYSTEM_PROMPT = """You are Loop Mentor, an AI tutor inside PracticeLoop, a spaced-repetition
learning app. Be encouraging, concise, and concrete.

Rules:
- If asked directly for the answer to a checkpoint or practice question, give a hint first
  (a nudge toward the reasoning) rather than the full answer -- only give the full answer
  if the student asks again after the hint.
- Base explanations on the context below when it's relevant; don't invent facts about
  material you weren't shown.
- Keep replies short -- a few sentences or a short list, not an essay.

{context_block}
"""


class ConversationNotFound(Exception):
    pass


def _build_context_block(context: dict | None) -> str:
    if not context:
        return "No specific lesson or path is open right now -- this is a general question."

    if context["type"] == "lesson":
        lines = [
            "The student is currently on this lesson:",
            f'Path: "{context["path_title"]}"',
            f'Module: "{context["module_title"]}"',
            f'Unit: "{context["unit_title"]}"',
            f'Lesson: "{context["lesson_title"]}"',
        ]
        if context["concept"]:
            lines.append(f"Concept covered: {context['concept']}")
        if context["checkpoint_question"]:
            lines.append(f"Checkpoint question: {context['checkpoint_question']}")
            lines.append(f"Checkpoint answer (see the hint-first rule above): {context['checkpoint_answer']}")
        return "\n".join(lines)

    if context["type"] == "path":
        return (
            f'The student is viewing their learning path "{context["path_title"]}" '
            f"({context['progress_percent']}% complete)."
        )

    # general
    if context.get("weak_topics"):
        lines = [f"- {t['topic']}: {t['mastery_score']}/100" for t in context["weak_topics"]]
        return "The student's weakest topics right now, by mastery score:\n" + "\n".join(lines)
    return "No specific lesson or path is open right now -- this is a general question."


async def build_context(pool: asyncpg.Pool, user_id: int, context_type: str, context_id: int | None) -> dict:
    """Ownership-checked -- a context_id the caller doesn't own (or that
    doesn't exist) just falls back to the general/ambient context rather
    than erroring the whole conversation; nothing about that context ever
    reaches the prompt either way."""
    if context_type == "lesson" and context_id:
        row = await pool.fetchrow(
            """SELECT l.title AS lesson_title, l.content,
                      u.title AS unit_title, m.title AS module_title, p.title AS path_title
               FROM learning_lessons l
               JOIN learning_units u ON u.unit_id = l.unit_id
               JOIN learning_modules m ON m.module_id = u.module_id
               JOIN learning_paths p ON p.path_id = m.path_id
               WHERE l.lesson_id = $1 AND p.user_id = $2""",
            context_id,
            user_id,
        )
        if row is not None:
            content = row["content"] or {}
            return {
                "type": "lesson",
                "path_title": row["path_title"],
                "module_title": row["module_title"],
                "unit_title": row["unit_title"],
                "lesson_title": row["lesson_title"],
                "concept": content.get("concept", ""),
                "checkpoint_question": content.get("checkpoint_question", ""),
                "checkpoint_answer": content.get("checkpoint_answer", ""),
            }

    if context_type == "path" and context_id:
        row = await pool.fetchrow(
            """SELECT p.title,
                      count(l.lesson_id) AS total_lessons, count(l.completed_at) AS completed_lessons
               FROM learning_paths p
               LEFT JOIN learning_modules m ON m.path_id = p.path_id
               LEFT JOIN learning_units u ON u.module_id = m.module_id
               LEFT JOIN learning_lessons l ON l.unit_id = u.unit_id
               WHERE p.path_id = $1 AND p.user_id = $2
               GROUP BY p.path_id""",
            context_id,
            user_id,
        )
        if row is not None:
            percent = (
                round(100 * row["completed_lessons"] / row["total_lessons"]) if row["total_lessons"] else 0
            )
            return {"type": "path", "path_title": row["title"], "progress_percent": percent}

    # general -- also the fallback for an unowned/missing lesson or path id.
    # local import: avoids a mentor -> dashboard cycle at module load time
    from app.dashboard.service import topic_mastery

    weak_topics = await topic_mastery(pool, user_id)
    return {"type": "general", "weak_topics": weak_topics[:3]}


async def get_or_create_conversation(
    pool: asyncpg.Pool, user_id: int, context_type: str, context_id: int | None
) -> int:
    if context_type not in VALID_CONTEXT_TYPES:
        context_type = "general"
    if context_type == "general":
        context_id = None

    row = await pool.fetchrow(
        """INSERT INTO mentor_conversations (user_id, context_type, context_id)
           VALUES ($1, $2, $3)
           ON CONFLICT (user_id, context_type, coalesce(context_id, -1))
           DO UPDATE SET user_id = EXCLUDED.user_id
           RETURNING conversation_id""",
        user_id,
        context_type,
        context_id,
    )
    return row["conversation_id"]


async def list_messages(pool: asyncpg.Pool, user_id: int, conversation_id: int) -> list[dict]:
    """Ownership-checked via the join -- an unowned conversation_id just
    returns an empty list rather than raising, since the panel's own
    load-on-open GET treats "nothing yet" and "not yours" the same way."""
    rows = await pool.fetch(
        """SELECT m.role, m.content, m.created_at FROM mentor_messages m
           JOIN mentor_conversations c ON c.conversation_id = m.conversation_id
           WHERE m.conversation_id = $1 AND c.user_id = $2
           ORDER BY m.created_at""",
        conversation_id,
        user_id,
    )
    return [dict(r) for r in rows]


async def send_message(
    pool: asyncpg.Pool,
    user_id: int,
    conversation_id: int,
    user_text: str,
    *,
    context: dict,
    ai_available: bool,
) -> str:
    """Persists the student's message and the reply either way -- a
    "needs an AI provider" or "couldn't generate a reply" message is
    still a real, honest turn in the conversation, not a silent drop."""
    owner = await pool.fetchval(
        "SELECT user_id FROM mentor_conversations WHERE conversation_id = $1", conversation_id
    )
    if owner != user_id:
        raise ConversationNotFound(conversation_id)

    await pool.execute(
        "INSERT INTO mentor_messages (conversation_id, role, content) VALUES ($1, 'user', $2)",
        conversation_id,
        user_text,
    )

    reply = None
    if ai_available:
        try:
            await consume_llm_budget(pool, user_id)
        except Exception:
            reply = _REPLY_BUDGET_EXHAUSTED

    if reply is None and not ai_available:
        reply = _REPLY_NEEDS_AI_PROVIDER

    if reply is None:
        history = await list_messages(pool, user_id, conversation_id)
        recent = history[-_MAX_HISTORY_MESSAGES:]
        convo_text = "\n".join(
            f"{'Student' if m['role'] == 'user' else 'Loop Mentor'}: {m['content']}" for m in recent
        )
        prompt = (
            _SYSTEM_PROMPT.format(context_block=_build_context_block(context))
            + "\n\nConversation so far:\n"
            + convo_text
            + "\n\nLoop Mentor:"
        )
        try:
            reply = await generate(prompt, temperature=0.5)
        except Exception:
            logger.warning("Mentor reply generation failed", exc_info=True)
            reply = _REPLY_GENERATION_FAILED

    await pool.execute(
        "INSERT INTO mentor_messages (conversation_id, role, content) VALUES ($1, 'assistant', $2)",
        conversation_id,
        reply,
    )
    await pool.execute(
        "UPDATE mentor_conversations SET updated_at = now() WHERE conversation_id = $1", conversation_id
    )
    return reply
