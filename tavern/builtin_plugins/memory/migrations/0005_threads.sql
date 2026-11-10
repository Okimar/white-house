-- Memory++ M5 (ADR 0019): «Слово и нити»: promises, vows and principles, claims about oneself or the world, debts and
-- agreements, secrets told (who knows what), quests. Names start with p_memory_ (plugins/schema.py checks it).
-- 0001-0004 are shipped and never edited.
--
-- A record: who gave their word (a character id; the user's persona: its character id, or 'user' in a chat without a
-- «me» member), to whom ('' = everyone / the world), the exact quote and the message it is in. It belongs to a branch
-- when its anchor message is on it (ADR 3.1): records made before a fork are shared. No foreign key on the message
-- ids (as with chapters, 0002): a deleted message only takes the record off the branches, and the assistant's undo of
-- that delete brings it back. locked = a hand edit: automatic work never changes it. deleted = removed by hand (it
-- can be restored).
CREATE TABLE p_memory_threads (
    id         TEXT PRIMARY KEY,
    chat_id    TEXT NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
    kind       TEXT NOT NULL,                 -- promise | vow | claim | debt | secret | quest
    who        TEXT NOT NULL,
    to_whom    TEXT NOT NULL DEFAULT '',
    knows      TEXT NOT NULL DEFAULT '[]',    -- a secret: who knew it when it was told (later: the states)
    quote      TEXT NOT NULL,
    message_id TEXT,
    anchor_id  TEXT NOT NULL,
    chapter_id TEXT NOT NULL DEFAULT '',      -- the chapter it was found in ('' = written by hand)
    importance INTEGER NOT NULL DEFAULT 2,    -- 1 minor .. 3 key to the story
    keywords   TEXT NOT NULL DEFAULT '[]',
    made_by    TEXT NOT NULL,                 -- auto | hand
    locked     INTEGER NOT NULL DEFAULT 0,
    deleted    INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
) STRICT;
CREATE INDEX p_memory_threads_chat ON p_memory_threads(chat_id);

-- Status changes are versions anchored at a message, so each branch has its own (ADR 3.1): the status on a branch is
-- the newest row anchored on it; none = 'active'. A row holds the whole state: status and who knows (a secret told to
-- one more person is a new row).
CREATE TABLE p_memory_thread_states (
    id         TEXT PRIMARY KEY,
    thread_id  TEXT NOT NULL REFERENCES p_memory_threads(id) ON DELETE CASCADE,
    chat_id    TEXT NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
    anchor_id  TEXT NOT NULL,
    status     TEXT NOT NULL,                 -- active | kept | broken | dropped
    knows      TEXT NOT NULL DEFAULT '[]',
    note       TEXT NOT NULL DEFAULT '',
    message_id TEXT,
    made_by    TEXT NOT NULL,                 -- auto | hand
    created_at TEXT NOT NULL
) STRICT;
CREATE INDEX p_memory_thread_states_chat ON p_memory_thread_states(chat_id, thread_id);

-- Which closed chapters the threads step already read (one request per chapter, in story order), as for layers (0004).
CREATE TABLE p_memory_thread_runs (
    chapter_id TEXT PRIMARY KEY REFERENCES p_memory_chapters(id) ON DELETE CASCADE,
    chat_id    TEXT NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
    state      TEXT NOT NULL,                 -- done | failed
    error      TEXT NOT NULL DEFAULT '',
    tries      INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
) STRICT;
CREATE INDEX p_memory_thread_runs_chat ON p_memory_thread_runs(chat_id);

-- A record that goes away takes the vector of its quote along.
CREATE TRIGGER p_memory_threads_gone AFTER DELETE ON p_memory_threads BEGIN
    DELETE FROM p_memory_vectors WHERE item_id = 't:' || OLD.id;
END;
