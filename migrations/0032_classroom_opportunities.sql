-- Academia-industry visibility: a teacher searches real, live job/internship
-- postings (the same Adzuna search discovery already uses, app/jobs/sources.py)
-- and shares the ones worth their students' attention straight to the
-- classroom. Deliberately not a new "employer" account/role -- that's a real,
-- separate trust and verification problem (who's allowed to post as a
-- company, moderation, applicant privacy) this migration doesn't attempt to
-- solve; a teacher curating real external postings is the honest, buildable
-- slice of that idea. No user_id: this is classroom content, like an
-- assignment, not any one person's private discovery.
CREATE TABLE IF NOT EXISTS classroom_opportunities (
  opportunity_id serial PRIMARY KEY,
  classroom_id   integer NOT NULL REFERENCES classrooms(classroom_id) ON DELETE CASCADE,
  title          text NOT NULL,
  company        text NOT NULL DEFAULT '',
  location       text NOT NULL DEFAULT '',
  description    text NOT NULL DEFAULT '',
  url            text NOT NULL DEFAULT '',
  posted_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_classroom_opportunities_classroom
  ON classroom_opportunities(classroom_id, posted_at DESC);
