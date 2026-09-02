-- Deployment-wide daily LLM call ceiling, alongside the per-user one in
-- 0002. The per-user budget bounds what any single account can spend, but
-- nothing bounded the total: signup is open, so N accounts x LLM_DAILY_BUDGET
-- is unbounded spend against one shared provider key. On a public demo that
-- is the deployment owner's quota, and exhausting it degrades every user's
-- experience at once rather than just the abuser's.
--
-- One row per day rather than a counter row that gets reset: the history is
-- useful (it is the only record of what a day actually cost) and an upsert on
-- the date is the same single round trip either way.
CREATE TABLE IF NOT EXISTS llm_usage_global (
  usage_date date PRIMARY KEY,
  call_count integer NOT NULL DEFAULT 0
);
