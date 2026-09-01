-- Phase 10: Practice Modes and Gamification (XP half). XP only for real
-- learning actions -- a practice attempt (free-text or multiple-choice),
-- a completed lesson, a taken diagnostic. Never for a page view or an
-- idle click.
--
-- The UNIQUE constraint makes awarding idempotent: re-toggling a lesson
-- complete/incomplete/complete again, or a retried request, can't be
-- used to farm XP for the same underlying event twice. A fresh practice
-- attempt always gets a fresh attempt_id, so genuine repeated practice
-- still earns XP every time -- only the *same* event is deduplicated.
CREATE TABLE IF NOT EXISTS xp_events (
  event_id    serial PRIMARY KEY,
  user_id     integer NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
  source_type text NOT NULL CHECK (source_type IN ('practice_attempt', 'lesson_complete', 'diagnostic')),
  source_id   integer NOT NULL,  -- the attempts.attempt_id / learning_lessons.lesson_id / diagnostic_attempts.attempt_id
  amount      integer NOT NULL CHECK (amount > 0),
  created_at  timestamptz NOT NULL DEFAULT now(),
  UNIQUE (user_id, source_type, source_id)
);
CREATE INDEX IF NOT EXISTS idx_xp_events_user ON xp_events(user_id);
