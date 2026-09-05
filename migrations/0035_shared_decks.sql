-- Shared decks: the community-content layer this app was missing entirely.
-- A student publishes a snapshot of their own bank (everything tagged
-- with one topic) as a named, described deck; anyone can browse the
-- public gallery and import a copy into their own bank. Deliberately a
-- SNAPSHOT (shared_deck_questions has its own copies of question/answer/
-- etc., not a live FK into `questions`), for two reasons: the owner
-- editing or deleting their own questions later must never silently
-- change or break a deck someone already imported, and a published deck
-- must never expose the owner's live, private question bank (which may
-- include personal capture notes) -- only what they explicitly snapshot
-- at publish time.
CREATE TABLE IF NOT EXISTS shared_decks (
  deck_id        serial PRIMARY KEY,
  owner_user_id  integer NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
  name           text NOT NULL,
  description    text NOT NULL DEFAULT '',
  topic          text NOT NULL DEFAULT '',
  question_count integer NOT NULL DEFAULT 0,
  import_count   integer NOT NULL DEFAULT 0,
  created_at     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_shared_decks_created ON shared_decks(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_shared_decks_owner ON shared_decks(owner_user_id);

CREATE TABLE IF NOT EXISTS shared_deck_questions (
  shared_deck_question_id serial PRIMARY KEY,
  deck_id              integer NOT NULL REFERENCES shared_decks(deck_id) ON DELETE CASCADE,
  question             text NOT NULL,
  answer               text NOT NULL DEFAULT '',
  example              text NOT NULL DEFAULT '',
  topic                text NOT NULL DEFAULT '',
  difficulty           text NOT NULL DEFAULT 'medium',
  code_snippet         text NOT NULL DEFAULT '',
  language             text NOT NULL DEFAULT '',
  question_type        text NOT NULL DEFAULT 'free_text',
  choices              jsonb,
  correct_choice_index integer
);
CREATE INDEX IF NOT EXISTS idx_shared_deck_questions_deck ON shared_deck_questions(deck_id);
