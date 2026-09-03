-- Wave 2 of the gap-audit fixes: make "today" the user's today, and stop
-- a good streak dying to a technicality.
--
-- day_rollover_hour: Anki's "next day starts at N". A learner who studies
-- past midnight sets it to 3 or 4 so a 1 AM session still counts for the
-- day before. 0 is a plain midnight boundary. Combined with the existing
-- (previously cosmetic-only) timezone column, this is what due_for_review,
-- the daily plan, the dashboard's "due today", and streak_days now key on
-- instead of the server's UTC date.
--
-- streak_shields: earned one per full week of a live streak, spent
-- automatically to cover a single missed day. Duolingo sells these because
-- losing a long streak to one bad day is the fastest way to lose the user.
ALTER TABLE profiles
  ADD COLUMN IF NOT EXISTS day_rollover_hour integer NOT NULL DEFAULT 0
    CHECK (day_rollover_hour BETWEEN 0 AND 23),
  ADD COLUMN IF NOT EXISTS streak_shields integer NOT NULL DEFAULT 0
    CHECK (streak_shields >= 0),
  ADD COLUMN IF NOT EXISTS streak_shield_week date;  -- ISO week already credited, so we grant at most one/week

-- user_badges: badges are still computed live from real counts (see
-- gamification/service.py) -- this table only records the first moment
-- each one crossed its threshold, which is what lets us fire a
-- "you earned X" notification exactly once instead of on every page load.
CREATE TABLE IF NOT EXISTS user_badges (
  user_id    integer NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
  badge_id   text NOT NULL,
  earned_at  timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, badge_id)
);

-- The Phase 15 notifications table pinned kind to a two-value CHECK.
-- Widen it: badge_earned (Wave 2), and streak_milestone for the 7/30/100
-- day marks. Drop-and-add rather than a fragile in-place edit.
ALTER TABLE notifications DROP CONSTRAINT IF EXISTS notifications_kind_check;
ALTER TABLE notifications ADD CONSTRAINT notifications_kind_check
  CHECK (kind IN ('assignment_created', 'guardian_accepted', 'badge_earned', 'streak_milestone'));
