-- A classroom leaderboard ranks members by XP -- real signal already
-- computed from xp_events, nothing new to track. leaderboard_opt_out
-- mirrors digest_opt_out's pattern: a student who'd rather not be ranked
-- in front of classmates just disappears from the list, not anonymized
-- to a confusing placeholder.
ALTER TABLE profiles ADD COLUMN IF NOT EXISTS leaderboard_opt_out boolean NOT NULL DEFAULT false;
