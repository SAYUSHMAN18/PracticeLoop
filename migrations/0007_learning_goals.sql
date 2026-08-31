-- Phase 2.3/2.4 (adapted to this app's existing single-tenant, adult-learner
-- scope): a goal type and target date give the dashboard something concrete
-- to count down to, instead of only ever showing "keep reviewing." Daily
-- time budget and timezone are stored for a future adaptive daily-plan
-- feature (Phase 3.2) to size a session against, not used yet.
ALTER TABLE profiles
  ADD COLUMN IF NOT EXISTS goal_type text NOT NULL DEFAULT '',
  ADD COLUMN IF NOT EXISTS target_date date,
  ADD COLUMN IF NOT EXISTS daily_time_budget_minutes integer,
  ADD COLUMN IF NOT EXISTS timezone text NOT NULL DEFAULT '';
