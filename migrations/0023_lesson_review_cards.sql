-- Phase 18: lessons feed the spaced-repetition queue.
--
-- "Spaced repetition is the spine" is one of this app's stated design
-- principles -- MCQs, free-text recall, and diagnostics all schedule
-- through the same FSRS engine. Lessons were the hole in it: a student
-- could work through a 40-lesson path, each lesson with a checkpoint
-- question, and have nothing in their review queue from any of it unless
-- they separately captured questions by hand.
--
-- Completing a lesson now turns its checkpoint into a review card, tied
-- back to the lesson here. Nullable because every question that already
-- exists (and every one captured by hand) has no lesson behind it.
-- ON DELETE CASCADE: deleting the path deletes the lessons deletes these
-- auto-generated cards -- they were never the student's own captures.
ALTER TABLE questions
  ADD COLUMN IF NOT EXISTS source_lesson_id integer
    REFERENCES learning_lessons(lesson_id) ON DELETE CASCADE;

-- One card per lesson: re-completing a lesson (toggle off, toggle back on,
-- or a double-submit) is a no-op, not a second identical card. Partial, so
-- the overwhelming majority of rows -- every hand-captured question, with a
-- NULL here -- are unconstrained.
CREATE UNIQUE INDEX IF NOT EXISTS idx_questions_source_lesson
  ON questions(source_lesson_id)
  WHERE source_lesson_id IS NOT NULL;
