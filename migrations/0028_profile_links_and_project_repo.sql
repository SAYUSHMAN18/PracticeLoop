-- Wave 11: connect your work.
--
-- The portfolio is meant to be shown to someone -- an employer, a
-- teacher -- but it had no way to point at the student themselves.
-- github_url / linkedin_url / website_url are set on the profile and
-- render as a links bar on the portfolio. Plain text columns, validated
-- in Python (must be https and, for the first two, the right host);
-- anything that doesn't pass is stored as "".
ALTER TABLE profiles
  ADD COLUMN IF NOT EXISTS github_url text NOT NULL DEFAULT '',
  ADD COLUMN IF NOT EXISTS linkedin_url text NOT NULL DEFAULT '',
  ADD COLUMN IF NOT EXISTS website_url text NOT NULL DEFAULT '';

-- A project already had submission_link, but only for a *submitted*
-- project and only in the submit form. repo_url is the code for a
-- project at any stage -- editable from the project page, shown there
-- and on the portfolio.
ALTER TABLE projects
  ADD COLUMN IF NOT EXISTS repo_url text NOT NULL DEFAULT '';
