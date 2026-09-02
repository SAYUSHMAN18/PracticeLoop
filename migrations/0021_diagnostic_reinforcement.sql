-- Phase 17: close the loop between a diagnostic result and a learning
-- path. Until now those were two disconnected features -- a diagnostic
-- told you which subtopics were weak and then dead-ended at a link to
-- "start a learning path", leaving the student to re-type the gaps by
-- hand. A diagnostic can now build a focus module of remediation lessons
-- straight into a path (existing or new), inserted at the top so the next
-- thing studied is the thing just measured as weakest.

-- A path can now originate from a diagnostic result, alongside a typed
-- goal and a subject template. Dropping and re-adding is how a CHECK
-- constraint is widened; IF EXISTS keeps this replayable against a
-- database where a previous attempt got partway.
ALTER TABLE learning_paths DROP CONSTRAINT IF EXISTS learning_paths_source_type_check;
ALTER TABLE learning_paths ADD CONSTRAINT learning_paths_source_type_check
  CHECK (source_type IN ('goal', 'template', 'diagnostic'));

-- Which diagnostic produced this module, if any. Nullable because every
-- module that already exists (and every one built from a goal or
-- template) has no diagnostic behind it. ON DELETE SET NULL rather than
-- CASCADE: the lessons are real work the student may have started, so
-- losing the provenance label is the right trade against deleting their
-- progress. Account deletion still removes both sides via users.
ALTER TABLE learning_modules
  ADD COLUMN IF NOT EXISTS source_attempt_id integer
    REFERENCES diagnostic_attempts(attempt_id) ON DELETE SET NULL;

-- One focus module per (path, diagnostic): re-submitting the same result
-- into the same path is a no-op rather than stacking duplicate modules,
-- which is what a double-click or a browser back-and-resubmit produces.
-- Partial, so the many rows with a NULL source_attempt_id are unconstrained.
CREATE UNIQUE INDEX IF NOT EXISTS idx_learning_modules_path_attempt
  ON learning_modules(path_id, source_attempt_id)
  WHERE source_attempt_id IS NOT NULL;
