-- Phase 13: Projects and Proof of Learning. A project (optionally tied to
-- a learning path) with a milestone checklist and a final submission that
-- gets AI rubric feedback -- real output beyond a mastery score.
--
-- No peer-feedback or teacher-feedback columns yet (both need real
-- multi-tenant users, Phase 14's own territory, not something to bolt on
-- here). No public/shareable link yet either -- an unauthenticated route
-- exposing student work needs its own deliberate privacy design, not a
-- boolean flag added as an afterthought; the portfolio this phase adds
-- stays private (authenticated, own-view only) until that's built.
CREATE TABLE IF NOT EXISTS projects (
  project_id      serial PRIMARY KEY,
  user_id         integer NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
  path_id         integer REFERENCES learning_paths(path_id) ON DELETE SET NULL,
  title           text NOT NULL,
  brief           text NOT NULL DEFAULT '',
  status          text NOT NULL DEFAULT 'in_progress' CHECK (status IN ('in_progress', 'submitted')),
  submission_text text NOT NULL DEFAULT '',
  submission_link text NOT NULL DEFAULT '',
  feedback        jsonb,
  created_at      timestamptz NOT NULL DEFAULT now(),
  submitted_at    timestamptz
);
CREATE INDEX IF NOT EXISTS idx_projects_user ON projects(user_id, created_at);

CREATE TABLE IF NOT EXISTS project_milestones (
  milestone_id serial PRIMARY KEY,
  project_id   integer NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
  title        text NOT NULL,
  position     integer NOT NULL,
  completed_at timestamptz
);
CREATE INDEX IF NOT EXISTS idx_project_milestones_project ON project_milestones(project_id, position);
