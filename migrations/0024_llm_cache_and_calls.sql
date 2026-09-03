-- Wave 1 of the gap-audit fixes: make the AI layer scale and be observable.
--
-- llm_cache: identical (provider, model, temperature, prompt) served from
-- here instead of a fresh call. Only the call sites whose prompt carries no
-- user data opt in -- a learning-path skeleton keyed on a goal string, a
-- lesson body keyed on four titles, a diagnostic keyed on a topic. Two
-- students starting "Learn Python" now share one generation instead of
-- paying for two.
CREATE TABLE IF NOT EXISTS llm_cache (
  cache_key   text PRIMARY KEY,          -- sha256(provider|model|temperature|prompt)
  response    text NOT NULL,
  model       text NOT NULL DEFAULT '',
  hit_count   integer NOT NULL DEFAULT 0,
  created_at  timestamptz NOT NULL DEFAULT now(),
  last_hit_at timestamptz
);
CREATE INDEX IF NOT EXISTS idx_llm_cache_created ON llm_cache(created_at);

-- llm_calls: one row per outbound call (or cache hit), with token counts
-- where the provider reports them. This is the difference between "we made
-- N calls today" and "today cost roughly $X" -- the budget table counts
-- calls, which says nothing about spend.
CREATE TABLE IF NOT EXISTS llm_calls (
  call_id           bigserial PRIMARY KEY,
  user_id           integer REFERENCES users(user_id) ON DELETE SET NULL,
  provider          text NOT NULL,
  model             text NOT NULL DEFAULT '',
  prompt_tokens     integer,
  completion_tokens integer,
  cached            boolean NOT NULL DEFAULT false,
  failed            boolean NOT NULL DEFAULT false,
  latency_ms        integer,
  created_at        timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_llm_calls_created ON llm_calls(created_at);
CREATE INDEX IF NOT EXISTS idx_llm_calls_user ON llm_calls(user_id, created_at);
