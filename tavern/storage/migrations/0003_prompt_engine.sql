-- Stage 4а: several characters per chat, prompt presets, and the archive of what the model saw.

-- Characters taking part in a chat. One list for everyone: the user's own character is a member
-- with is_me = 1. depth NULL = card at the start of the prompt, N = N messages from the end.
-- importance: 1 = most important ... 10 = dropped first when the prompt does not fit.
-- model_may_write = 0 (only meaningful for is_me) tells the model not to write for that character.
CREATE TABLE chat_members (
    chat_id         TEXT NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
    character_id    TEXT NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
    position        INTEGER NOT NULL DEFAULT 0,
    depth           INTEGER CHECK (depth IS NULL OR depth BETWEEN 0 AND 1000),
    importance      INTEGER NOT NULL DEFAULT 5 CHECK (importance BETWEEN 1 AND 10),
    is_me           INTEGER NOT NULL DEFAULT 0,
    model_may_write INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (chat_id, character_id)
) STRICT;
CREATE INDEX idx_chat_members_character ON chat_members(character_id);

INSERT INTO chat_members (chat_id, character_id)
SELECT id, character_id FROM chats WHERE character_id IS NOT NULL;

-- Prompt presets: ordered blocks + model parameters, as JSON (see core/presets.py).
CREATE TABLE presets (
    id         TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    data       TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
) STRICT;

-- "What the model saw": every distinct piece of prompt text is stored once (zlib, keyed by its
-- hash) and never changed; a record lists which pieces one reply was generated from.
CREATE TABLE prompt_pieces (
    hash    TEXT PRIMARY KEY,
    content BLOB NOT NULL,
    size    INTEGER NOT NULL
) STRICT;

-- Which versions a source (a preset block, a character card, a message) has had, for the
-- read-only version viewer.
CREATE TABLE prompt_piece_sources (
    source     TEXT NOT NULL,
    hash       TEXT NOT NULL REFERENCES prompt_pieces(hash) ON DELETE CASCADE,
    first_seen TEXT NOT NULL,
    PRIMARY KEY (source, hash)
) STRICT;
CREATE INDEX idx_prompt_piece_sources_hash ON prompt_piece_sources(hash);

-- One record per generated reply version. To stay small, a record may reuse the first `keep`
-- entries of an earlier record of the same chat (`base_id`) and list only what differs.
-- Records are never updated, so a base never changes under the records built on it.
CREATE TABLE prompt_records (
    id         INTEGER PRIMARY KEY,
    chat_id    TEXT NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
    message_id TEXT NOT NULL,
    swipe_idx  INTEGER NOT NULL,
    base_id    INTEGER,
    keep       INTEGER NOT NULL DEFAULT 0,
    chain      INTEGER NOT NULL DEFAULT 0,
    entries    BLOB NOT NULL,
    meta       TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
) STRICT;
CREATE INDEX idx_prompt_records_message ON prompt_records(message_id, swipe_idx, id);
CREATE INDEX idx_prompt_records_chat ON prompt_records(chat_id, id);
