-- Phase 13: project milestones and submissions are their own real XP
-- source, not a same-source_type-different-sign-of-id hack layered onto
-- 'practice_attempt'/'diagnostic' -- that would corrupt source_type as
-- an honest "why did I get this XP" label, which is the whole point of
-- tracking it per-event in the first place.
ALTER TABLE xp_events DROP CONSTRAINT xp_events_source_type_check;
ALTER TABLE xp_events ADD CONSTRAINT xp_events_source_type_check
  CHECK (source_type IN ('practice_attempt', 'lesson_complete', 'diagnostic', 'project_milestone', 'project_submitted'));
