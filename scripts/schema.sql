CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS users (
  user_id       serial PRIMARY KEY,
  email         text UNIQUE NOT NULL,
  password_hash text NOT NULL,
  name          text NOT NULL,
  created_at    timestamptz NOT NULL DEFAULT now()
);

-- One tenant per app instance, so this is a 1:1 detail table on users,
-- not a separately-keyed profile row like CareerOS's multi-tenant version.
CREATE TABLE IF NOT EXISTS profiles (
  user_id         integer PRIMARY KEY REFERENCES users(user_id) ON DELETE CASCADE,
  target_role     text NOT NULL DEFAULT '',
  target_companies text NOT NULL DEFAULT '',
  resume_text     text NOT NULL DEFAULT '',
  updated_at      timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS questions (
  question_id  serial PRIMARY KEY,
  user_id      integer NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
  question     text NOT NULL,
  answer       text NOT NULL DEFAULT '',
  example      text NOT NULL DEFAULT '',
  topic        text NOT NULL DEFAULT '',
  difficulty   text NOT NULL DEFAULT 'medium' CHECK (difficulty IN ('easy', 'medium', 'hard')),
  company      text NOT NULL DEFAULT '',
  code_snippet text NOT NULL DEFAULT '',
  language     text NOT NULL DEFAULT '',
  source       text NOT NULL DEFAULT 'manual',   -- manual | ai_generated
  embedding    vector(384),
  created_at   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS attempts (
  attempt_id        serial PRIMARY KEY,
  question_id       integer NOT NULL REFERENCES questions(question_id) ON DELETE CASCADE,
  user_id           integer NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
  confidence_rating smallint NOT NULL CHECK (confidence_rating BETWEEN 1 AND 5),
  feedback          text NOT NULL DEFAULT '',
  practiced_at      timestamptz NOT NULL DEFAULT now(),
  next_review_at    date NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_questions_user ON questions(user_id);
CREATE INDEX IF NOT EXISTS idx_attempts_question ON attempts(question_id);
CREATE INDEX IF NOT EXISTS idx_attempts_user_next_review ON attempts(user_id, next_review_at);

-- No ANN index on the embedding column: at the scale a single student's
-- question bank realistically reaches (dozens-to-low-thousands), a sequential
-- scan is exact and fast. Revisit only if this ever needs to serve many
-- tenants' pooled question banks (see docs/adr/001-lineage-and-scope.md).
