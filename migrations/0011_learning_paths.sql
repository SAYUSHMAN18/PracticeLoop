-- Phase 6: Learning Paths and Course Architecture. A path is a goal (or,
-- later, a document/job description/template) turned into a navigable
-- skeleton of modules -> units -> lessons.
--
-- Lesson *content* -- the concept/example/practice/checkpoint block types
-- from Phase 7 -- isn't stored yet; a lesson here is just a titled,
-- markable-done step. completed_at is included from day one anyway (not
-- bolted on once blocks exist) so "mark this lesson done" is real
-- progress tracking as soon as a path has lessons at all.
--
-- No skill/prerequisite tables yet: the plan's "soft prerequisites" unit
-- behavior needs a real skill graph (Phase 5.1-equivalent decision,
-- already flagged elsewhere as needing its own dedicated scoping) that
-- this migration deliberately doesn't try to sneak in as a side effect.

CREATE TABLE IF NOT EXISTS learning_paths (
  path_id       serial PRIMARY KEY,
  user_id       integer NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
  title         text NOT NULL,
  source_type   text NOT NULL DEFAULT 'goal' CHECK (source_type IN ('goal', 'template')),
  source_detail text NOT NULL DEFAULT '',  -- the goal text typed in, or the template's id
  ai_generated  boolean NOT NULL DEFAULT false,
  created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS learning_modules (
  module_id   serial PRIMARY KEY,
  path_id     integer NOT NULL REFERENCES learning_paths(path_id) ON DELETE CASCADE,
  title       text NOT NULL,
  description text NOT NULL DEFAULT '',
  position    integer NOT NULL,
  created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS learning_units (
  unit_id     serial PRIMARY KEY,
  module_id   integer NOT NULL REFERENCES learning_modules(module_id) ON DELETE CASCADE,
  title       text NOT NULL,
  description text NOT NULL DEFAULT '',
  position    integer NOT NULL,
  created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS learning_lessons (
  lesson_id    serial PRIMARY KEY,
  unit_id      integer NOT NULL REFERENCES learning_units(unit_id) ON DELETE CASCADE,
  title        text NOT NULL,
  position     integer NOT NULL,
  completed_at timestamptz,
  created_at   timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_learning_paths_user ON learning_paths(user_id);
CREATE INDEX IF NOT EXISTS idx_learning_modules_path ON learning_modules(path_id);
CREATE INDEX IF NOT EXISTS idx_learning_units_module ON learning_units(module_id);
CREATE INDEX IF NOT EXISTS idx_learning_lessons_unit ON learning_lessons(unit_id);
