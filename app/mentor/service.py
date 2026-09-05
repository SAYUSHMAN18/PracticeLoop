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
    """Finds or creates the *active* session (ended_at IS NULL) for this
    (user, context) -- calling this twice in a row without starting a new
    chat or resuming an old one always returns the same conversation_id,
    same as before sessions existed."""
    if context_type not in VALID_CONTEXT_TYPES:
        context_type = "general"
    if context_type == "general":
        context_id = None

    row = await pool.fetchrow(
        """INSERT INTO mentor_conversations (user_id, context_type, context_id)
           VALUES ($1, $2, $3)
           ON CONFLICT (user_id, context_type, coalesce(context_id, -1)) WHERE ended_at IS NULL
           DO UPDATE SET user_id = EXCLUDED.user_id
           RETURNING conversation_id""",
        user_id,
        context_type,
        context_id,
    )
    return row["conversation_id"]


async def start_new_chat(pool: asyncpg.Pool, user_id: int, context_type: str, context_id: int | None) -> int:
    """Ends whichever session is currently active for this (user, context)
    -- if any -- and opens a fresh one. The ended session's messages are
    left exactly as they are; list_sessions is how a student finds their
    way back to it. A no-op if the active session has no messages yet --
    starting fresh from an already-empty chat would just leave a pointless
    empty row behind, and list_sessions filters those out anyway."""
    current = await get_or_create_conversation(pool, user_id, context_type, context_id)
    has_messages = await pool.fetchval(
        "SELECT EXISTS (SELECT 1 FROM mentor_messages WHERE conversation_id = $1)", current
    )
    if not has_messages:
        return current

    if context_type not in VALID_CONTEXT_TYPES:
        context_type = "general"
    if context_type == "general":
        context_id = None

    await pool.execute("UPDATE mentor_conversations SET ended_at = now() WHERE conversation_id = $1", current)
    return await get_or_create_conversation(pool, user_id, context_type, context_id)


async def switch_to_session(pool: asyncpg.Pool, user_id: int, conversation_id: int) -> int:
    """Resumes a past session: un-ends it and ends whatever else is
    currently active for the same (user, context), preserving the
    at-most-one-active-session invariant get_or_create_conversation relies
    on. The session picked up this way keeps its original created_at --
    only its ended_at moves, back to NULL."""
    convo = await pool.fetchrow(
        "SELECT context_type, context_id FROM mentor_conversations "
        "WHERE conversation_id = $1 AND user_id = $2",
        conversation_id,
        user_id,
    )
    if convo is None:
        raise ConversationNotFound(conversation_id)

    await pool.execute(
        """UPDATE mentor_conversations SET ended_at = now()
           WHERE user_id = $1 AND context_type = $2 AND coalesce(context_id, -1) = coalesce($3, -1)
             AND ended_at IS NULL""",
        user_id,
        convo["context_type"],
        convo["context_id"],
    )
    await pool.execute(
        "UPDATE mentor_conversations SET ended_at = NULL WHERE conversation_id = $1", conversation_id
    )
    return conversation_id


async def clear_conversation(pool: asyncpg.Pool, user_id: int, conversation_id: int) -> None:
    """Wipes a session's messages in place -- distinct from start_new_chat,
    which keeps the old messages around under a new conversation_id. This
    is the "start over, and don't keep what was here" action."""
    owner = await pool.fetchval(
        "SELECT user_id FROM mentor_conversations WHERE conversation_id = $1", conversation_id
    )
    if owner != user_id:
        raise ConversationNotFound(conversation_id)
    await pool.execute("DELETE FROM mentor_messages WHERE conversation_id = $1", conversation_id)


async def list_sessions(
    pool: asyncpg.Pool, user_id: int, context_type: str, context_id: int | None
) -> list[dict]:
    """Past, ended sessions for this (user, context) -- the active one
    (rendered by _render_conversation itself) is deliberately excluded."""
    if context_type not in VALID_CONTEXT_TYPES:
        context_type = "general"
    if context_type == "general":
        context_id = None

    rows = await pool.fetch(
        """SELECT c.conversation_id, c.created_at, c.ended_at,
                  (SELECT count(*) FROM mentor_messages m
                     WHERE m.conversation_id = c.conversation_id) AS message_count,
                  (SELECT m.content FROM mentor_messages m
                     WHERE m.conversation_id = c.conversation_id AND m.role = 'user'
                     ORDER BY m.created_at LIMIT 1) AS preview
           FROM mentor_conversations c
           WHERE c.user_id = $1 AND c.context_type = $2 AND coalesce(c.context_id, -1) = coalesce($3, -1)
             AND c.ended_at IS NOT NULL
             AND EXISTS (SELECT 1 FROM mentor_messages m WHERE m.conversation_id = c.conversation_id)
           ORDER BY c.ended_at DESC
           LIMIT 20""",
        user_id,
        context_type,
        context_id,
    )
    return [dict(r) for r in rows]


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
