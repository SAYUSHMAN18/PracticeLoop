-- Phase 14, scoped to teacher classrooms and consent-based guardian
-- views. Community (study groups, peer explanations, moderation) is
-- explicitly NOT attempted here -- it's a genuinely separate scope
-- (real-time-ish collaboration, a moderation/reporting policy) that
-- deserves its own dedicated pass, not a shallow bolt-on alongside
-- everything else in this phase.
--
-- role lives directly on users (not a separate roles table) -- this app
-- has exactly one elevated, self-declared role (teacher), and it never
-- changes what a user can do to their OWN data, only what UI unlocks
-- (creating a classroom). Nothing here grants access to another user's
-- data by role alone -- every cross-user view below is gated by an
-- explicit join code or an explicit accepted invite, never by role.
ALTER TABLE users ADD COLUMN IF NOT EXISTS role text NOT NULL DEFAULT 'student' CHECK (role IN ('student', 'teacher'));

CREATE TABLE IF NOT EXISTS classrooms (
  classroom_id    serial PRIMARY KEY,
  teacher_user_id integer NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
  name            text NOT NULL,
  join_code       text NOT NULL UNIQUE,
  created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_classrooms_teacher ON classrooms(teacher_user_id);

-- A student joins by entering a join_code themselves -- a teacher can
-- never add a student directly, matching the plan's own "avoid invasive
-- surveillance" instruction for this whole phase.
CREATE TABLE IF NOT EXISTS classroom_members (
  classroom_id    integer NOT NULL REFERENCES classrooms(classroom_id) ON DELETE CASCADE,
  student_user_id integer NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
  joined_at       timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (classroom_id, student_user_id)
);

CREATE TABLE IF NOT EXISTS assignments (
  assignment_id serial PRIMARY KEY,
  classroom_id  integer NOT NULL REFERENCES classrooms(classroom_id) ON DELETE CASCADE,
  title         text NOT NULL,
  description   text NOT NULL DEFAULT '',
  topic         text NOT NULL DEFAULT '',
  due_date      date,
  created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_assignments_classroom ON assignments(classroom_id, due_date);

-- Guardian access is student-initiated, never teacher- or
-- admin-initiated: the student generates an invite link and shares it
-- themselves (this app has no outbound-email infrastructure, so "share
-- the link yourself" is the honest mechanism, not a simulated email
-- send). guardian_user_id stays null until someone actually opens the
-- link and accepts it while logged in -- that's the consent moment.
CREATE TABLE IF NOT EXISTS guardian_links (
  link_id          serial PRIMARY KEY,
  student_user_id  integer NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
  guardian_user_id integer REFERENCES users(user_id) ON DELETE CASCADE,
  invite_token     text NOT NULL UNIQUE,
  status           text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'accepted', 'revoked')),
  created_at       timestamptz NOT NULL DEFAULT now(),
  accepted_at      timestamptz
);
CREATE INDEX IF NOT EXISTS idx_guardian_links_student ON guardian_links(student_user_id);
CREATE INDEX IF NOT EXISTS idx_guardian_links_guardian ON guardian_links(guardian_user_id) WHERE guardian_user_id IS NOT NULL;
