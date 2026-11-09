CREATE TABLE api_keys (
    id uuid PRIMARY KEY,
    client_id uuid NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    position integer NOT NULL CHECK (position BETWEEN 1 AND 5),
    label text NOT NULL DEFAULT '' CHECK (char_length(label) <= 30),
    key_enc bytea NOT NULL,
    last4 text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(client_id, position) DEFERRABLE INITIALLY DEFERRED
);
INSERT INTO api_keys(id,client_id,position,label,key_enc,last4)
SELECT id,id,1,'',api_key_enc,COALESCE(api_key_last4,'') FROM clients WHERE api_key_enc IS NOT NULL;
ALTER TABLE messages ADD COLUMN key_index integer;
ALTER TABLE messages ADD COLUMN key_failover jsonb;
