-- Loop Mentor: multiple chat sessions per (user, context) instead of one
-- permanent thread. ended_at IS NULL marks the session still active/
-- current -- get_or_create_conversation only ever finds or creates among
-- those rows, so the existing "same conversation every time you reopen
-- the panel" behavior is unchanged unless someone actually asks for a new
-- chat. "New chat" (app/mentor/service.py start_new_chat) stamps
-- ended_at on the current session and opens a fresh one; the ended
-- session's messages are untouched -- that's the record list_sessions
-- reads to show past chats a student can tap back into.
ALTER TABLE mentor_conversations ADD COLUMN IF NOT EXISTS ended_at timestamptz;

-- Replaces the old always-unique index: at most one *active* session per
-- (user, context) still holds, but ended sessions can pile up freely.
DROP INDEX IF EXISTS idx_mentor_conversations_identity;
CREATE UNIQUE INDEX IF NOT EXISTS idx_mentor_conversations_active
  ON mentor_conversations(user_id, context_type, coalesce(context_id, -1))
  WHERE ended_at IS NULL;
