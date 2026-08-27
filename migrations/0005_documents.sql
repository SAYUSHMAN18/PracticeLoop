-- Student document vault -- resumes, transcripts, certificates, cover
-- letters, anything else worth keeping in one place. Distinct from
-- profiles.resume_text (the single "active" resume used for job-fit
-- scoring, gap analysis, and tailoring): uploading a document tagged
-- 'resume' here also refreshes profiles.resume_text, but this table is
-- where every version and every other kind of record actually lives and
-- can be downloaded back, not just matched against.
CREATE TABLE IF NOT EXISTS documents (
  document_id    serial PRIMARY KEY,
  user_id        integer NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
  doc_type       text NOT NULL DEFAULT 'other'
                 CHECK (doc_type IN ('resume', 'transcript', 'certificate', 'cover_letter', 'other')),
  title          text NOT NULL,
  filename       text NOT NULL,
  mime_type      text NOT NULL DEFAULT 'application/octet-stream',
  size_bytes     integer NOT NULL,
  content_bytes  bytea NOT NULL,
  extracted_text text NOT NULL DEFAULT '',
  created_at     timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_documents_user ON documents(user_id, created_at DESC);
