ALTER TABLE conversations
    ADD COLUMN reasoning_effort text CHECK (reasoning_effort IN ('low', 'high', 'max')),
    ADD COLUMN thinking boolean;

ALTER TABLE messages
    ADD COLUMN reasoning_effort text CHECK (reasoning_effort IN ('low', 'high', 'max'));
