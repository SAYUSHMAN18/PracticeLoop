-- Wave 6 of the gap-audit fixes: confirm the email is real, and use it to
-- pull lapsed learners back.
--
-- email_verified_at: signup sends a verification link. The app stays
-- usable unverified (a hard gate on day one loses people who mistype an
-- address and never see the mail), but the re-engagement digest only
-- goes to verified addresses -- no point emailing a typo'd or fake one,
-- and it keeps bounce rates down.
--
-- last_digest_at: dedup, so a twice-daily cron can't send the same person
-- two "cards are due" mails in one day.
ALTER TABLE users
  ADD COLUMN IF NOT EXISTS email_verified_at timestamptz,
  ADD COLUMN IF NOT EXISTS last_digest_at timestamptz;

-- digest_opt_out: set from the profile page, or from the one-click
-- unsubscribe link in every digest email.
ALTER TABLE profiles
  ADD COLUMN IF NOT EXISTS digest_opt_out boolean NOT NULL DEFAULT false;

-- Parallel to password_reset_tokens (migration 0026), same shape: only
-- the sha256 of the emailed token is stored, single use, and there's no
-- expiry check here because an unverified account isn't a security risk
-- the way a live reset link is -- a stale verify link just works late.
CREATE TABLE IF NOT EXISTS email_verification_tokens (
  token_hash text PRIMARY KEY,
  user_id    integer NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
  created_at timestamptz NOT NULL DEFAULT now(),
  used_at    timestamptz
);
CREATE INDEX IF NOT EXISTS idx_email_verification_user ON email_verification_tokens(user_id);
