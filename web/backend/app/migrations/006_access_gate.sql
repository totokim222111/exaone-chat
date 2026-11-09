CREATE TABLE access_users (
    username text PRIMARY KEY,
    password_hash text NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE access_grants (
    id uuid PRIMARY KEY,
    token_hash bytea UNIQUE NOT NULL,
    username text NOT NULL REFERENCES access_users(username) ON DELETE CASCADE,
    created_at timestamptz NOT NULL DEFAULT now(),
    last_seen_at timestamptz NOT NULL DEFAULT now(),
    user_agent text NOT NULL CHECK (length(user_agent) <= 200),
    ip text NOT NULL,
    revoked_at timestamptz
);
CREATE INDEX access_grants_user_created ON access_grants(username,created_at DESC);
