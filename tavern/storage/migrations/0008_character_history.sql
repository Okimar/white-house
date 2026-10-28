-- History of a character: every change of the card or of its bubbles (not their place on the map)
-- is a step that "Отменить" can take back, with no limit. Steps are grouped into versions: a
-- version closes when the character is closed or after 10 quiet minutes, and can be restored
-- as a whole. A step keeps only what the changed things were before it (zlib-packed JSON), so
-- the history stays small however long it gets.
CREATE TABLE character_versions (
    id           INTEGER PRIMARY KEY,
    character_id TEXT NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
    started_at   TEXT NOT NULL,
    closed_at    TEXT
) STRICT;
CREATE INDEX idx_character_versions ON character_versions(character_id, id);

-- undone = 1: taken back with "Отменить"; such steps are dropped by the next new change.
CREATE TABLE character_steps (
    id           INTEGER PRIMARY KEY,
    character_id TEXT NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
    version_id   INTEGER NOT NULL REFERENCES character_versions(id) ON DELETE CASCADE,
    at           TEXT NOT NULL,
    what         TEXT NOT NULL,
    before       BLOB NOT NULL,
    undone       INTEGER NOT NULL DEFAULT 0
) STRICT;
CREATE INDEX idx_character_steps ON character_steps(character_id, id);
