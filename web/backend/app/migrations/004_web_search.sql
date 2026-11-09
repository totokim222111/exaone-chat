ALTER TABLE messages ADD COLUMN search jsonb;
ALTER TABLE conversations ADD COLUMN web_search boolean;
ALTER TABLE clients
    ADD COLUMN tavily_key_enc bytea,
    ADD COLUMN tavily_key_last4 text,
    ADD COLUMN brave_key_enc bytea,
    ADD COLUMN brave_key_last4 text;
