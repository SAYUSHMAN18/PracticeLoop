-- Phase 2.1: JD -> skill gap analysis. Each row is one skill extracted from
-- a job description, bucketed against what the user's resume claims and
-- what their own practice history actually shows they recall.
CREATE TABLE IF NOT EXISTS job_skill_gaps (
  gap_id     serial PRIMARY KEY,
  user_id    integer NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
  listing_id integer REFERENCES job_listings(listing_id) ON DELETE SET NULL,
  skill      text NOT NULL,
  bucket     text NOT NULL CHECK (bucket IN ('proven', 'untested', 'missing')),
  evidence   text NOT NULL DEFAULT '',
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_job_skill_gaps_user ON job_skill_gaps(user_id, created_at DESC);
