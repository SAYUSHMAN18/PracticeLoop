-- Phase 2.1 step 6 ("select current proficiency"). Not the full diagnostic
-- test (Phase 2.1 step 10 / Phase 5.2) -- that's an actual adaptive
-- assessment feature, not a form field. This is the honest, cheap version:
-- a self-reported starting point, which is at least as informative as no
-- signal at all and costs nothing to build.
ALTER TABLE profiles
  ADD COLUMN IF NOT EXISTS proficiency_level text NOT NULL DEFAULT '';
