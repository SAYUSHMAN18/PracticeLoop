-- Phase 2.1, scoped down: not the plan's full 11-step wizard (that would
-- fight the existing signup flow's own "under a minute" design and its
-- Phase 2 completion criteria) -- one optional goal-setting screen shown
-- once after signup or first login, skippable, never shown again either
-- way. This flag is what makes "skip" actually stick instead of
-- re-prompting forever.
ALTER TABLE profiles
  ADD COLUMN IF NOT EXISTS onboarding_completed boolean NOT NULL DEFAULT false;
