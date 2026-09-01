-- Phase 15: a lightweight in-app notification feed. No push/email
-- infrastructure exists in this app (see Phase 14's guardian invites
-- for the same reasoning) -- these are read on next visit, not pushed
-- in real time. Scoped to the two clean, single-point-trigger events
-- this app actually has where the affected person likely isn't already
-- looking at the result: a new assignment posted to a classroom you're
-- in, and a guardian invite you sent being accepted. Badge-earned
-- notifications are a real follow-up (they'd need a before/after diff
-- at every XP-awarding call site, not a single trigger point) --
-- deliberately not attempted here.
CREATE TABLE IF NOT EXISTS notifications (
  notification_id serial PRIMARY KEY,
  user_id          integer NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
  kind             text NOT NULL CHECK (kind IN ('assignment_created', 'guardian_accepted')),
  title            text NOT NULL,
  body             text NOT NULL DEFAULT '',
  link             text NOT NULL DEFAULT '',
  read_at          timestamptz,
  created_at       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_notifications_user_unread ON notifications(user_id, read_at, created_at);
