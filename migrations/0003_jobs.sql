-- Phase 1: scheduled job discovery, fit scoring, and an application tracker.
-- job_listings is deliberately scoped per-user (not a shared pool) -- each
-- user's discovery run searches against their own profile.target_role, and
-- UNIQUE (user_id, source, external_id) is what makes a retried or double-
-- fired cron run insert nothing twice.

CREATE TABLE IF NOT EXISTS job_runs (
  run_id          serial PRIMARY KEY,
  started_at      timestamptz NOT NULL DEFAULT now(),
  finished_at     timestamptz,
  status          text NOT NULL DEFAULT 'running' CHECK (status IN ('running', 'success', 'partial', 'failed')),
  users_processed integer NOT NULL DEFAULT 0,
  listings_found  integer NOT NULL DEFAULT 0,
  error           text
);

CREATE TABLE IF NOT EXISTS job_listings (
  listing_id    serial PRIMARY KEY,
  user_id       integer NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
  source        text NOT NULL,
  external_id   text NOT NULL,
  title         text NOT NULL,
  company       text NOT NULL DEFAULT '',
  location      text NOT NULL DEFAULT '',
  description   text NOT NULL DEFAULT '',
  url           text NOT NULL DEFAULT '',
  fit_score     smallint CHECK (fit_score IS NULL OR fit_score BETWEEN 0 AND 100),
  fit_method    text CHECK (fit_method IS NULL OR fit_method IN ('llm', 'keyword')),
  discovered_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (user_id, source, external_id)
);

CREATE TABLE IF NOT EXISTS applications (
  application_id serial PRIMARY KEY,
  user_id        integer NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
  listing_id     integer REFERENCES job_listings(listing_id) ON DELETE SET NULL,
  company        text NOT NULL,
  role           text NOT NULL,
  status         text NOT NULL DEFAULT 'applied'
                   CHECK (status IN ('applied', 'interviewing', 'offer', 'rejected', 'withdrawn')),
  fit_score      smallint CHECK (fit_score IS NULL OR fit_score BETWEEN 0 AND 100),
  applied_at     timestamptz NOT NULL DEFAULT now(),
  follow_up_at   date,
  interview_at   timestamptz,
  notes          text NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_job_listings_user_score ON job_listings(user_id, fit_score DESC NULLS LAST);
CREATE INDEX IF NOT EXISTS idx_applications_user ON applications(user_id);
CREATE INDEX IF NOT EXISTS idx_applications_follow_up ON applications(user_id, follow_up_at);
