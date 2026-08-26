-- Per-user daily LLM call budget. Deliberately not in-memory: the app can run
-- multiple workers (WEB_CONCURRENCY) and gets redeployed/spun-down on free-tier
-- hosting, both of which would silently reset an in-memory counter and let a
-- user blow through the limit by just waiting for a restart.
CREATE TABLE IF NOT EXISTS llm_usage (
  user_id    integer NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
  usage_date date NOT NULL,
  call_count integer NOT NULL DEFAULT 0,
  PRIMARY KEY (user_id, usage_date)
);
