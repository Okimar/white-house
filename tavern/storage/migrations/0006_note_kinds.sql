-- Stage 4в, after the owner's review: three kinds of notes and "через N ответов".
--   once    : sent only in the request it fired in, never kept (was 'hidden');
--   hidden  : kept in the history after its message, not shown in the chat unless asked;
--   visible : kept in the history and shown next to its message (was 'saved').
-- interval is now how many replies to skip between two sendings: 0 = every reply (was 1).
-- Marks of where a 'once' note went are dropped: such notes leave nothing behind.

CREATE TABLE author_notes_new (
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
    interval     INTEGER NOT NULL DEFAULT 0 CHECK (interval BETWEEN 0 AND 1000),
    mode         TEXT NOT NULL DEFAULT 'once' CHECK (mode IN ('once', 'hidden', 'visible')),
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    CHECK ((scope = 'global' AND character_id IS NULL AND chat_id IS NULL)
        OR (scope = 'character' AND character_id IS NOT NULL AND chat_id IS NULL)
        OR (scope = 'chat' AND chat_id IS NOT NULL AND character_id IS NULL))
) STRICT;
INSERT INTO author_notes_new
SELECT id, scope, character_id, chat_id, position, title, text, enabled, depth, role, interval - 1,
       CASE mode WHEN 'saved' THEN 'visible' ELSE 'once' END, created_at, updated_at
FROM author_notes;
DROP TABLE author_notes;
ALTER TABLE author_notes_new RENAME TO author_notes;
CREATE INDEX idx_author_notes_character ON author_notes(character_id);
CREATE INDEX idx_author_notes_chat ON author_notes(chat_id);

-- Notes kept in the history after a message. note_id: the note it came from (NULL = by hand).
CREATE TABLE message_notes_new (
    id         TEXT PRIMARY KEY,
    chat_id    TEXT NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
    message_id TEXT NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    kind       TEXT NOT NULL CHECK (kind IN ('hidden', 'visible')),
    note_id    TEXT,
    title      TEXT NOT NULL DEFAULT '',
    role       TEXT NOT NULL DEFAULT 'system' CHECK (role IN ('user', 'assistant', 'system')),
    text       TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (message_id, note_id)
) STRICT;
INSERT INTO message_notes_new
SELECT id, chat_id, message_id, 'visible', note_id, title, role, text, created_at
FROM message_notes WHERE kind = 'saved';
DROP TABLE message_notes;
ALTER TABLE message_notes_new RENAME TO message_notes;
CREATE INDEX idx_message_notes_chat ON message_notes(chat_id, kind);
