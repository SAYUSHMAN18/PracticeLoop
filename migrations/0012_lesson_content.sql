-- Phase 7: Interactive Lesson Experience. A lesson from Phase 6 was just a
-- checkable-off title; this gives it real content -- concept explanation,
-- worked example, a checkpoint question (with a hidden-until-revealed
-- answer), and a summary. Generated lazily on first open (not all at once
-- when the path is created, which would multiply the path-creation LLM
-- cost by the lesson count) and cached here so opening a lesson twice
-- doesn't regenerate it.
ALTER TABLE learning_lessons
  ADD COLUMN IF NOT EXISTS content jsonb;
