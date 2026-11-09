ALTER TABLE messages ADD COLUMN usage jsonb;

-- Preserve existing titles: previous schema did not record manual renames.
ALTER TABLE conversations ADD COLUMN title_manual boolean NOT NULL DEFAULT false;
UPDATE conversations SET title_manual = true;

-- Legacy settings are migrated by db.py in this same transaction, using env defaults.
