-- Stage 4б: a character is its card (the root) plus a tree of blocks.

-- mode: 'always' (blue, sent every time), 'search' (green, sent when the topic comes up by
-- keywords or meaning), 'off' (grey). depth NULL = next to the card, N = N messages from the end.
-- sticky: how many replies a found block stays. layout: the bubble map (x, y, display line/nested).
-- extra: fields from an imported SillyTavern lorebook entry, written back on export.
CREATE TABLE character_blocks (
    id           TEXT PRIMARY KEY,
    character_id TEXT NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
    parent_id    TEXT REFERENCES character_blocks(id) ON DELETE CASCADE,
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
    updated_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
) STRICT;
CREATE INDEX idx_character_blocks_owner ON character_blocks(character_id, parent_id, position);

-- Meaning vectors of a block's own text (title path + content only), per embedding model.
-- text_hash tells when the block changed and its vector must be made again.
CREATE TABLE block_embeddings (
    block_id  TEXT NOT NULL REFERENCES character_blocks(id) ON DELETE CASCADE,
    model     TEXT NOT NULL,
    text_hash TEXT NOT NULL,
    vector    BLOB NOT NULL,
    PRIMARY KEY (block_id, model)
) STRICT;

-- When a green block was last found in a chat (generation number), for "stays N replies".
CREATE TABLE chat_block_hits (
    chat_id  TEXT NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
    block_id TEXT NOT NULL REFERENCES character_blocks(id) ON DELETE CASCADE,
    hit_gen  INTEGER NOT NULL,
    PRIMARY KEY (chat_id, block_id)
) STRICT;
