-- Phase 9: Diagnostic and Adaptive Learning. A short, fixed-length
-- (8-question) multiple-choice diagnostic on a topic, scored
-- deterministically and mapped to a proficiency level. Not the plan's
-- fully adaptive difficulty-during-the-quiz version -- a fixed spread of
-- easy/medium/hard generated up front is the honest scope here; true
-- per-answer branching is a real follow-up, not something to fake.
--
-- The diagnostic's own questions are never persisted (they're one-off
-- and session-scoped -- see app/assessments/router.py) since they're not
-- meant to enter the spaced-repetition bank. Only the *result* of taking
-- one is durable, here.
CREATE TABLE IF NOT EXISTS diagnostic_attempts (
  attempt_id          serial PRIMARY KEY,
  user_id             integer NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
  topic               text NOT NULL,
  correct_count       integer NOT NULL,
  total_count         integer NOT NULL,
  proficiency_result  text NOT NULL,
  weak_subtopics      jsonb NOT NULL DEFAULT '[]',
  created_at          timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_diagnostic_attempts_user ON diagnostic_attempts(user_id, created_at);

-- Distinguishes a measured result from the self-reported dropdown
-- (Phase 2.1) that was there before any real diagnostic existed --
-- the profile page's proficiency_level column is shared by both, but the
-- dashboard/profile UI should be honest about which kind it's showing.
ALTER TABLE profiles
  ADD COLUMN IF NOT EXISTS proficiency_source text NOT NULL DEFAULT 'self_reported'
    CHECK (proficiency_source IN ('self_reported', 'diagnostic'));
