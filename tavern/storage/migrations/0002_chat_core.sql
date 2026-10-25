-- Characters: card data is the V2 `data` object (unknown keys preserved), `spec` remembers the
-- origin format so export can round-trip.
CREATE TABLE characters (
    id         TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    spec       TEXT NOT NULL DEFAULT 'chara_card_v2',
    data       TEXT NOT NULL,
    avatar     TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
) STRICT;

-- Saved API connections. The API key lives in secrets.json under "connection:<id>:api_key".
CREATE TABLE connections (
    id         TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    kind       TEXT NOT NULL,
    base_url   TEXT NOT NULL,
    model      TEXT NOT NULL DEFAULT '',
    params     TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
) STRICT;

-- A chat is a tree of messages. `leaf_id` is the tip of the active branch (no foreign key:
-- it points into messages, which points back here).
CREATE TABLE chats (
    id           TEXT PRIMARY KEY,
    character_id TEXT REFERENCES characters(id) ON DELETE SET NULL,
    title        TEXT NOT NULL,
    leaf_id      TEXT,
    meta         TEXT NOT NULL DEFAULT '{}',
    created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
) STRICT;
CREATE INDEX idx_chats_character ON chats(character_id, updated_at DESC);
CREATE INDEX idx_chats_updated ON chats(updated_at DESC);

-- One row per node. `depth` is the distance from the root, so the active path is ordered by it.
-- hidden: not shown in the chat. exclude_from_prompt: never sent to the model.
-- owner: plugin id that created a service message ('' = core).
CREATE TABLE messages (
    id                  TEXT PRIMARY KEY,
    chat_id             TEXT NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
    parent_id           TEXT REFERENCES messages(id) ON DELETE CASCADE,
    depth               INTEGER NOT NULL,
    role                TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'system')),
    name                TEXT NOT NULL DEFAULT '',
    hidden              INTEGER NOT NULL DEFAULT 0,
    exclude_from_prompt INTEGER NOT NULL DEFAULT 0,
    owner               TEXT NOT NULL DEFAULT '',
    active_swipe        INTEGER NOT NULL DEFAULT 0,
    extra               TEXT NOT NULL DEFAULT '{}',
    created_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
) STRICT;
CREATE INDEX idx_messages_chat_parent ON messages(chat_id, parent_id);
CREATE INDEX idx_messages_parent ON messages(parent_id);

-- Variants of one message. The active one is messages.active_swipe.
CREATE TABLE swipes (
    message_id TEXT NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    idx        INTEGER NOT NULL,
    content    TEXT NOT NULL,
    meta       TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    PRIMARY KEY (message_id, idx)
) STRICT, WITHOUT ROWID;
