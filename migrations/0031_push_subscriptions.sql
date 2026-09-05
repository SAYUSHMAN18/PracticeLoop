-- Web push: a browser's PushManager.subscribe() returns an endpoint URL
-- plus two keys (p256dh, auth) unique to that browser installation. One
-- row per subscribed device/browser, not per user, since the same person
-- can enable notifications on a phone and a laptop independently and
-- each needs its own push sent.
CREATE TABLE IF NOT EXISTS push_subscriptions (
  subscription_id serial PRIMARY KEY,
  user_id         integer NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
  endpoint        text NOT NULL UNIQUE,
  p256dh          text NOT NULL,
  auth            text NOT NULL,
  created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_push_subscriptions_user ON push_subscriptions(user_id);
