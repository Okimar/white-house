-- The journal of the assistant's changes outlives its conversation: it is there to take changes back.
-- chat_id becomes nullable (ON DELETE SET NULL); chat_ref keeps the conversation's id for good (it
-- groups the actions of one conversation), chat_title keeps its name, filled when it is deleted.
-- req_id / req_text keep the user's request the action belongs to (the latest plain message of theirs
-- before it), written when the action happens, so the commits stay apart, with their text, after the
-- messages are gone. Rows of conversations already deleted before this migration cannot be filled.
CREATE TABLE assistant_actions_new (
    id         INTEGER PRIMARY KEY,
    chat_id    TEXT REFERENCES assistant_chats(id) ON DELETE SET NULL,
    chat_ref   TEXT NOT NULL,
    chat_title TEXT NOT NULL DEFAULT '',
    req_id     INTEGER NOT NULL DEFAULT 0,
    req_text   TEXT NOT NULL DEFAULT '',
    tool       TEXT NOT NULL,
    args       TEXT NOT NULL DEFAULT '{}',
    what       TEXT NOT NULL DEFAULT '',
    undo       TEXT NOT NULL DEFAULT '{}',
    status     TEXT NOT NULL DEFAULT 'done',
    created_at TEXT NOT NULL
) STRICT;

INSERT INTO assistant_actions_new (id, chat_id, chat_ref, chat_title, req_id, req_text, tool, args, what, undo, status, created_at)
SELECT a.id, a.chat_id, a.chat_id,
       CASE WHEN trim(c.title) <> '' THEN trim(c.title) ELSE 'Беседа от ' || substr(c.created_at, 1, 10) END,
       COALESCE((SELECT MAX(m.id) FROM assistant_messages m WHERE m.chat_id = a.chat_id AND m.role = 'user'
                 AND m.data = '{}' AND m.text NOT IN ('Давай', 'Продолжай') AND m.created_at <= a.created_at), 0),
       COALESCE((SELECT m2.text FROM assistant_messages m2 WHERE m2.id = (SELECT MAX(m.id) FROM assistant_messages m
                 WHERE m.chat_id = a.chat_id AND m.role = 'user' AND m.data = '{}'
                 AND m.text NOT IN ('Давай', 'Продолжай') AND m.created_at <= a.created_at)), ''),
       a.tool, a.args, a.what, a.undo, a.status, a.created_at
FROM assistant_actions a LEFT JOIN assistant_chats c ON c.id = a.chat_id;

DROP TABLE assistant_actions;
ALTER TABLE assistant_actions_new RENAME TO assistant_actions;
CREATE INDEX idx_assistant_actions_chat ON assistant_actions(chat_ref, id);
