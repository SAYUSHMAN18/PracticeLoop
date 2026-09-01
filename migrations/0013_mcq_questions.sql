-- Phase 8: Unified Practice Engine. Adds a second question type
-- (multiple_choice) alongside the existing free-text one, deterministically
-- gradable without an LLM -- the natural format for Phase 9's diagnostic
-- and Phase 10's quiz modes, both of which need instant, no-AI-required
-- scoring. Not attempting the plan's full 19-format list here: MCQ is the
-- one format everything downstream (diagnostics, quiz arena) actually
-- needs next.
ALTER TABLE questions
  ADD COLUMN IF NOT EXISTS question_type text NOT NULL DEFAULT 'free_text'
    CHECK (question_type IN ('free_text', 'multiple_choice')),
  ADD COLUMN IF NOT EXISTS choices jsonb,               -- multiple_choice only: a JSON array of option strings
  ADD COLUMN IF NOT EXISTS correct_choice_index smallint; -- multiple_choice only: 0-based index into choices
