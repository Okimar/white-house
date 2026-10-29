-- Stage 5: lorebooks. A lorebook is a book of bubbles, like a character's tree, that belongs to no
-- character. Bubbles, links, vectors, hits and history now have an owner: a character or a book.

-- settings: the book's own options, e.g. {"scan": 3}; extra: fields of an imported SillyTavern
-- book we do not use, written back on export.
CREATE TABLE lorebooks (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    settings    TEXT NOT NULL DEFAULT '{}',
    extra       TEXT NOT NULL DEFAULT '{}',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
) STRICT;

-- Where a book works: everywhere (global), in one chat, or wherever a character takes part
-- (as a character of the chat or as the user's own "это я").
CREATE TABLE lorebook_uses (
    id           INTEGER PRIMARY KEY,
    lorebook_id  TEXT NOT NULL REFERENCES lorebooks(id) ON DELETE CASCADE,
    scope        TEXT NOT NULL CHECK (scope IN ('global', 'chat', 'character')),
    chat_id      TEXT REFERENCES chats(id) ON DELETE CASCADE,
    character_id TEXT REFERENCES characters(id) ON DELETE CASCADE,
    position     INTEGER NOT NULL DEFAULT 0,
    CHECK ((scope = 'global' AND chat_id IS NULL AND character_id IS NULL)
        OR (scope = 'chat' AND chat_id IS NOT NULL AND character_id IS NULL)
        OR (scope = 'character' AND character_id IS NOT NULL AND chat_id IS NULL))
) STRICT;
CREATE UNIQUE INDEX idx_lorebook_uses_one ON lorebook_uses(lorebook_id, scope, COALESCE(chat_id, ''), COALESCE(character_id, ''));
CREATE INDEX idx_lorebook_uses_chat ON lorebook_uses(chat_id);
CREATE INDEX idx_lorebook_uses_character ON lorebook_uses(character_id);

-- Bubbles get a second possible owner. owner_id is whichever is set. rules: the finer options of
-- a bubble (second keywords, whole words, pause, delay, groups, filter, place, chains), checked
-- by services/character_tree.py.
-- The old tables are copied out first: dropping a table with rows that point at it would delete
-- those rows too (foreign keys are on while migrations run).
PRAGMA defer_foreign_keys = ON;

CREATE TABLE character_blocks_new (
    id           TEXT PRIMARY KEY,
    character_id TEXT REFERENCES characters(id) ON DELETE CASCADE,
    lorebook_id  TEXT REFERENCES lorebooks(id) ON DELETE CASCADE,
    owner_id     TEXT GENERATED ALWAYS AS (COALESCE(character_id, lorebook_id)) VIRTUAL,
    parent_id    TEXT REFERENCES character_blocks_new(id) ON DELETE CASCADE,
    position     INTEGER NOT NULL DEFAULT 0,
    title        TEXT NOT NULL,
    content      TEXT NOT NULL DEFAULT '',
    mode         TEXT NOT NULL DEFAULT 'search' CHECK (mode IN ('always', 'search', 'off')),
    keywords     TEXT NOT NULL DEFAULT '[]',
    depth        INTEGER CHECK (depth IS NULL OR depth BETWEEN 0 AND 1000),
    role         TEXT NOT NULL DEFAULT 'system' CHECK (role IN ('system', 'user', 'assistant')),
    sticky       INTEGER NOT NULL DEFAULT 3 CHECK (sticky BETWEEN 0 AND 1000),
    layout       TEXT NOT NULL DEFAULT '{}',
    extra        TEXT NOT NULL DEFAULT '{}',
    created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    detached     INTEGER NOT NULL DEFAULT 0,
    chance       INTEGER NOT NULL DEFAULT 100 CHECK (chance BETWEEN 0 AND 100),
    every        INTEGER NOT NULL DEFAULT 0 CHECK (every BETWEEN 0 AND 1000),
    rules        TEXT NOT NULL DEFAULT '{}',
    CHECK ((character_id IS NULL) <> (lorebook_id IS NULL))
) STRICT;
INSERT INTO character_blocks_new (id, character_id, parent_id, position, title, content, mode, keywords,
                                  depth, role, sticky, layout, extra, created_at, updated_at, detached,
                                  chance, every)
SELECT id, character_id, parent_id, position, title, content, mode, keywords, depth, role, sticky,
       layout, extra, created_at, updated_at, detached, chance, every
FROM character_blocks;

CREATE TABLE block_embeddings_new (
    block_id  TEXT NOT NULL REFERENCES character_blocks_new(id) ON DELETE CASCADE,
    model     TEXT NOT NULL,
    text_hash TEXT NOT NULL,
    vector    BLOB NOT NULL,
    PRIMARY KEY (block_id, model)
) STRICT;
INSERT INTO block_embeddings_new SELECT block_id, model, text_hash, vector FROM block_embeddings;

CREATE TABLE chat_block_hits_new (
    chat_id  TEXT NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
    block_id TEXT NOT NULL REFERENCES character_blocks_new(id) ON DELETE CASCADE,
    hit_gen  INTEGER NOT NULL,
    missed   INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (chat_id, block_id)
) STRICT;
INSERT INTO chat_block_hits_new SELECT chat_id, block_id, hit_gen, missed FROM chat_block_hits;

-- A link lives as long as both its bubbles; owner_id only groups links for listing.
CREATE TABLE block_links_new (
    owner_id TEXT NOT NULL,
    a        TEXT NOT NULL REFERENCES character_blocks_new(id) ON DELETE CASCADE,
    b        TEXT NOT NULL REFERENCES character_blocks_new(id) ON DELETE CASCADE,
    PRIMARY KEY (a, b),
    CHECK (a < b)
) STRICT;
INSERT INTO block_links_new SELECT character_id, a, b FROM block_links;

DROP TABLE block_embeddings;
DROP TABLE chat_block_hits;
DROP TABLE block_links;
DROP TABLE character_blocks;
ALTER TABLE character_blocks_new RENAME TO character_blocks;
ALTER TABLE block_embeddings_new RENAME TO block_embeddings;
ALTER TABLE chat_block_hits_new RENAME TO chat_block_hits;
ALTER TABLE block_links_new RENAME TO block_links;
CREATE INDEX idx_character_blocks_owner ON character_blocks(owner_id, parent_id, position);
CREATE INDEX idx_character_blocks_character ON character_blocks(character_id);
CREATE INDEX idx_character_blocks_lorebook ON character_blocks(lorebook_id);
CREATE INDEX idx_block_links_owner ON block_links(owner_id);
CREATE INDEX idx_block_links_b ON block_links(b);

-- History of a character or a book. Rows go when their owner is deleted (triggers below: the
-- owner can be in either table, so there is no single foreign key for it).
CREATE TABLE character_versions_new (
    id         INTEGER PRIMARY KEY,
    owner_id   TEXT NOT NULL,
    started_at TEXT NOT NULL,
    closed_at  TEXT
) STRICT;
INSERT INTO character_versions_new SELECT id, character_id, started_at, closed_at FROM character_versions;

CREATE TABLE character_steps_new (
    id         INTEGER PRIMARY KEY,
    owner_id   TEXT NOT NULL,
    version_id INTEGER NOT NULL REFERENCES character_versions_new(id) ON DELETE CASCADE,
    at         TEXT NOT NULL,
    what       TEXT NOT NULL,
    before     BLOB NOT NULL,
    undone     INTEGER NOT NULL DEFAULT 0
) STRICT;
INSERT INTO character_steps_new SELECT id, character_id, version_id, at, what, before, undone FROM character_steps;

DROP TABLE character_steps;
DROP TABLE character_versions;
ALTER TABLE character_versions_new RENAME TO character_versions;
ALTER TABLE character_steps_new RENAME TO character_steps;
CREATE INDEX idx_character_versions ON character_versions(owner_id, id);
CREATE INDEX idx_character_steps ON character_steps(owner_id, id);

CREATE TRIGGER character_history_gone AFTER DELETE ON characters BEGIN
    DELETE FROM character_versions WHERE owner_id = OLD.id;
END;
CREATE TRIGGER lorebook_history_gone AFTER DELETE ON lorebooks BEGIN
    DELETE FROM character_versions WHERE owner_id = OLD.id;
END;
