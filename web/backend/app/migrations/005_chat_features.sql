CREATE EXTENSION IF NOT EXISTS pg_trgm;
ALTER TABLE messages ADD COLUMN timing jsonb;
ALTER TABLE conversations ADD COLUMN starred boolean NOT NULL DEFAULT false;
CREATE TABLE attachments (
    id uuid PRIMARY KEY,
    client_id uuid NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    message_id uuid REFERENCES messages(id) ON DELETE CASCADE,
    name text NOT NULL,
    mime text NOT NULL,
    size integer NOT NULL,
    kind text NOT NULL,
    sha256 bytea NOT NULL,
    text text NOT NULL,
    raw bytea NOT NULL,
    text_chars integer NOT NULL,
    truncated boolean NOT NULL,
    position integer NOT NULL DEFAULT 0,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX attachments_client_message ON attachments(client_id, message_id);
CREATE INDEX attachments_unbound_created ON attachments(created_at) WHERE message_id IS NULL;
CREATE INDEX conversations_title_trgm ON conversations USING gin(title gin_trgm_ops);
CREATE INDEX messages_content_trgm ON messages USING gin(content gin_trgm_ops);
CREATE INDEX attachments_name_trgm ON attachments USING gin(name gin_trgm_ops);
CREATE TABLE shares (
    token text PRIMARY KEY,
    client_id uuid NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    -- Deliberately no conversation FK: deleting a conversation preserves its snapshot.
    conversation_id uuid NOT NULL,
    title text NOT NULL,
    snapshot jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    revoked_at timestamptz
);
CREATE INDEX shares_client_created ON shares(client_id, created_at DESC);
