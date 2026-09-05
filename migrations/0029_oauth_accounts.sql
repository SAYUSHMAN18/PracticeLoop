-- Google Sign-In: an account created this way has no password at all, and
-- password_hash has been NOT NULL since the baseline schema. oauth_provider
-- records how the account authenticates (empty = password, same convention
-- as every other "unset" text column in this schema) -- app/auth/router.py's
-- login form uses it to tell a Google-only account "use Google to sign in"
-- instead of a generic wrong-password error.
ALTER TABLE users ALTER COLUMN password_hash DROP NOT NULL;
ALTER TABLE users ADD COLUMN IF NOT EXISTS oauth_provider text NOT NULL DEFAULT '';
