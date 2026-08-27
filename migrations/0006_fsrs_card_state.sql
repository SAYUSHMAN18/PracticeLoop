-- Replaces the fixed-multiplier SM-2-style scheduler (app/practice/
-- spaced_repetition.py) with FSRS -- a memory model (stability,
-- difficulty, retrievability) fit to real forgetting-curve data, instead
-- of hand-picked growth constants. One row per question holds that
-- question's current FSRS memory state; `attempts` keeps logging the full
-- review history unchanged (streak/mastery queries still read from there).
CREATE TABLE IF NOT EXISTS card_states (
  question_id integer PRIMARY KEY REFERENCES questions(question_id) ON DELETE CASCADE,
  user_id     integer NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
  state       smallint NOT NULL DEFAULT 2,  -- fsrs.State: 1=Learning 2=Review 3=Relearning
  stability   double precision,
  difficulty  double precision,
  due         timestamptz,
  last_review timestamptz,
  updated_at  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_card_states_user_due ON card_states(user_id, due);

-- Backfill existing questions from their most recent attempt, so the
-- switch doesn't dump every existing card into "due right now" -- the old
-- interval becomes an approximate initial stability (not a scientifically
-- equivalent value, but a reasonable prior that keeps due dates roughly
-- where they were) and difficulty starts at FSRS's own neutral midpoint.
INSERT INTO card_states (question_id, user_id, state, stability, difficulty, due, last_review)
SELECT DISTINCT ON (a.question_id)
  a.question_id,
  a.user_id,
  2,
  GREATEST(1.0, (a.next_review_at - a.practiced_at::date)::double precision),
  5.0,
  a.next_review_at::timestamptz,
  a.practiced_at
FROM attempts a
ORDER BY a.question_id, a.practiced_at DESC
ON CONFLICT (question_id) DO NOTHING;
