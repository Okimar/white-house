-- Stage 8б: the assistant over the chat. Conversations with it are their own list, like chats.
CREATE TABLE assistant_chats (
    id         TEXT PRIMARY KEY,
    title      TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
) STRICT;

-- role: user (what the person wrote), assistant (what the assistant said, with the actions it
-- asked for in data), tool (what those actions returned, sent back to the model as data).
CREATE TABLE assistant_messages (
    id         INTEGER PRIMARY KEY,
    chat_id    TEXT NOT NULL REFERENCES assistant_chats(id) ON DELETE CASCADE,
    role       TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'tool')),
    text       TEXT NOT NULL DEFAULT '',
    data       TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
) STRICT;

CREATE INDEX idx_assistant_messages_chat ON assistant_messages(chat_id, id);

-- The journal: every change the assistant made, with what is needed to take it back.
-- undo: JSON (storage/snapshot.py rows); status: done | undone | failed | denied.
CREATE TABLE assistant_actions (
    id         INTEGER PRIMARY KEY,
    chat_id    TEXT NOT NULL REFERENCES assistant_chats(id) ON DELETE CASCADE,
    tool       TEXT NOT NULL,
    args       TEXT NOT NULL DEFAULT '{}',
    what       TEXT NOT NULL DEFAULT '',
    undo       TEXT NOT NULL DEFAULT '{}',
    status     TEXT NOT NULL DEFAULT 'done',
    created_at TEXT NOT NULL
) STRICT;

CREATE INDEX idx_assistant_actions_chat ON assistant_actions(chat_id, id);
