-- Memory++ M1 (ADR 0019): chat vectors. Everything starts with p_memory_ (plugins/schema.py checks it).
-- Chapters, summaries and the rest arrive in later migrations (0002...), never by editing this file.

-- Per-chat switches. A chat without a row has memory off.
CREATE TABLE p_memory_chats (
    chat_id    TEXT PRIMARY KEY REFERENCES chats(id) ON DELETE CASCADE,
    enabled    INTEGER NOT NULL DEFAULT 0,
    settings   TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL
) STRICT;

-- Full text cut along message boundaries (a long message splits into parts). The text itself is not kept: it
-- is read from the message, and text_hash tells when a piece has gone stale. first_id = last_id in M1 (one
-- message per piece); the pair is there for the chapter stage.
CREATE TABLE p_memory_pieces (
    id         TEXT PRIMARY KEY,
    chat_id    TEXT NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
    first_id   TEXT NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    last_id    TEXT NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    part       INTEGER NOT NULL DEFAULT 0,
    text_hash  TEXT NOT NULL,
    tokens     INTEGER NOT NULL,
    keywords   TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL
) STRICT;
CREATE INDEX p_memory_pieces_chat ON p_memory_pieces(chat_id, last_id);
CREATE UNIQUE INDEX p_memory_pieces_part ON p_memory_pieces(first_id, part);

-- Vectors: never exported, never journaled for the assistant's undo (the name ends in _vectors).
CREATE TABLE p_memory_vectors (
    item_id   TEXT NOT NULL,
    chat_id   TEXT NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
    model     TEXT NOT NULL,
    text_hash TEXT NOT NULL,
    vector    BLOB NOT NULL,
    PRIMARY KEY (item_id, model)
) STRICT;
CREATE INDEX p_memory_vectors_chat ON p_memory_vectors(chat_id, model);

-- A piece that goes away (its message was deleted, or its text changed shape) takes its vectors along.
CREATE TRIGGER p_memory_pieces_gone AFTER DELETE ON p_memory_pieces BEGIN
    DELETE FROM p_memory_vectors WHERE item_id = OLD.id;
END;
