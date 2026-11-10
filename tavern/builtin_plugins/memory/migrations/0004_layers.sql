-- Memory++ M4 (ADR 0019): «Развитие персонажа» and «Сейчас:» lines for lorebook bubbles. Names start with p_memory_
-- (plugins/schema.py checks it). 0001-0003 are shipped and never edited.
--
-- The texts themselves live in p_memory_texts (0003) as versions anchored at a message (the branch rule of ADR 3.1):
--   kind 'development': target = a character id (the user's persona: its character id, or 'user' when the chat has
--                       no «me» member); anchor_id = the last message of the chapter it was made after.
--   kind 'now_line':    target = a lorebook bubble (block) id; an empty text = the line was removed on this branch.
-- The value on a branch is the newest version whose anchor is on it. locked = a hand edit: automatic work never
-- replaces it.
--
-- Which closed chapters the development step already worked on (one request per chapter, in story order). A chapter
-- that goes away takes its row along; a failed one is retried after a backoff (tries, updated_at).
CREATE TABLE p_memory_layer_runs (
    chapter_id TEXT PRIMARY KEY REFERENCES p_memory_chapters(id) ON DELETE CASCADE,
    chat_id    TEXT NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
    state      TEXT NOT NULL,                 -- done | failed
    error      TEXT NOT NULL DEFAULT '',
    tries      INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
) STRICT;
CREATE INDEX p_memory_layer_runs_chat ON p_memory_layer_runs(chat_id);

-- Layers and lines are read per branch by (chat, kind, anchor): an index for it.
CREATE INDEX p_memory_texts_anchor ON p_memory_texts(chat_id, kind, anchor_id);
