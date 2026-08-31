-- Phase 4.3 (library UI), narrowed: favorites + type filtering are the
-- two pieces of real value for a vault this app's own scale actually has
-- (a handful to a few dozen files per student, not thousands) -- rigid
-- folders and a full semantic-search index are more machinery than a
-- vault this size needs; a client-side title filter covers "find it
-- fast" instead.
ALTER TABLE documents
  ADD COLUMN IF NOT EXISTS is_favorite boolean NOT NULL DEFAULT false;
