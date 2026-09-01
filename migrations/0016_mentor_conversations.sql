-- Phase 11: Loop Mentor as a real AI service. One conversation per
-- (user, context) -- opening the mentor on a specific lesson gets its
-- own persistent thread, separate from the general/no-specific-context
-- thread, so switching pages doesn't lose either. get_or_create
-- semantics live in app/mentor/service.py.
CREATE TABLE IF NOT EXISTS mentor_conversations (
  conversation_id serial PRIMARY KEY,
  user_id         integer NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
  context_type    text NOT NULL DEFAULT 'general' CHECK (context_type IN ('general', 'lesson', 'path')),
  context_id      integer,  -- learning_lessons.lesson_id or learning_paths.path_id, per context_type; null for 'general'
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_mentor_conversations_identity
  ON mentor_conversations(user_id, context_type, coalesce(context_id, -1));

CREATE TABLE IF NOT EXISTS mentor_messages (
  message_id      serial PRIMARY KEY,
  conversation_id integer NOT NULL REFERENCES mentor_conversations(conversation_id) ON DELETE CASCADE,
  role            text NOT NULL CHECK (role IN ('user', 'assistant')),
  content         text NOT NULL,
  created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_mentor_messages_conversation ON mentor_messages(conversation_id, created_at);
