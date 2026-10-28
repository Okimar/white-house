-- Stage 4в: author's notes.

-- A note is written once and sent again and again: on every reply or every `interval` replies,
-- `depth` messages from the end of the chat, as `role`. Three levels: global (no owner), a
-- character's (follows the character into every chat), a chat's.
-- mode 'hidden': the model sees it only in the request it fired in; 'saved': it fires once at a
-- place and stays in the history there (message_notes).
CREATE TABLE author_notes (
    id           TEXT PRIMARY KEY,
    scope        TEXT NOT NULL CHECK (scope IN ('global', 'character', 'chat')),
    character_id TEXT REFERENCES characters(id) ON DELETE CASCADE,
    chat_id      TEXT REFERENCES chats(id) ON DELETE CASCADE,
    position     INTEGER NOT NULL DEFAULT 0,
    title        TEXT NOT NULL DEFAULT '',
    text         TEXT NOT NULL DEFAULT '',
    enabled      INTEGER NOT NULL DEFAULT 1,
    depth        INTEGER NOT NULL DEFAULT 4 CHECK (depth BETWEEN 0 AND 1000),
    role         TEXT NOT NULL DEFAULT 'system' CHECK (role IN ('user', 'assistant', 'system')),
    interval     INTEGER NOT NULL DEFAULT 1 CHECK (interval BETWEEN 1 AND 1000),
    mode         TEXT NOT NULL DEFAULT 'hidden' CHECK (mode IN ('hidden', 'saved')),
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    CHECK ((scope = 'global' AND character_id IS NULL AND chat_id IS NULL)
        OR (scope = 'character' AND character_id IS NOT NULL AND chat_id IS NULL)
        OR (scope = 'chat' AND chat_id IS NOT NULL AND character_id IS NULL))
) STRICT;
CREATE INDEX idx_author_notes_character ON author_notes(character_id);
CREATE INDEX idx_author_notes_chat ON author_notes(chat_id);

-- Notes attached to a message: they sit right after it.
-- kind 'saved': part of the history, the model sees it in every later request.
-- kind 'mark': where a hidden note went; only shown to the user on request, never sent.
-- note_id: the note it came from (NULL = written by hand); no foreign key, it outlives the note.
CREATE TABLE message_notes (
    id         TEXT PRIMARY KEY,
    chat_id    TEXT NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
    message_id TEXT NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    kind       TEXT NOT NULL CHECK (kind IN ('saved', 'mark')),
    note_id    TEXT,
    title      TEXT NOT NULL DEFAULT '',
    role       TEXT NOT NULL DEFAULT 'system' CHECK (role IN ('user', 'assistant', 'system')),
    text       TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (message_id, note_id, kind)
) STRICT;
CREATE INDEX idx_message_notes_chat ON message_notes(chat_id, kind);
