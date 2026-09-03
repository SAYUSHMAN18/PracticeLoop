-- Wave 4 of the gap-audit fixes: account recovery, and per-account
-- brute-force lockout.
--
-- Lockout is per-account, layered on the existing per-IP rate limit
-- (core/middleware.py): the IP limit slows one host, this stops a
-- distributed guess against one specific account. N consecutive failed
-- logins lock it for a cool-off; any successful login clears the count,
-- and so does a password reset.
ALTER TABLE users
  ADD COLUMN IF NOT EXISTS failed_login_count integer NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS locked_until timestamptz;

-- password_reset_tokens: the emailed link carries a random token; only
-- its sha256 is stored, so a leaked database row can't be used to reset
-- anyone's password. One hour to use (created_at), single use (used_at),
-- and setting a new password deletes every other pending token for that
-- user.
CREATE TABLE IF NOT EXISTS password_reset_tokens (
  token_hash text PRIMARY KEY,
  user_id    integer NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
  created_at timestamptz NOT NULL DEFAULT now(),
  used_at    timestamptz
);
CREATE INDEX IF NOT EXISTS idx_password_reset_user ON password_reset_tokens(user_id);
