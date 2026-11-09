-- Memory++ M2 (ADR 0019): chapters. A chapter is a run of messages on one path, stored by its first and last
-- message id. Names start with p_memory_ (plugins/schema.py checks it).
--
-- first_id / last_id have NO foreign key to messages on purpose. The event «message deleted» fires AFTER the row
-- is gone, so a cascade would wipe the chapter before the handler could move its ends to the neighbours (ADR 3.2,
-- «the chapter survives»). The job drops a chapter whose ends really do not exist any more. A deleted chat still
-- takes its chapters along (chat_id cascades).
CREATE TABLE p_memory_chapters (
    id           TEXT PRIMARY KEY,
    chat_id      TEXT NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
    first_id     TEXT NOT NULL,
    last_id      TEXT NOT NULL,
    title        TEXT NOT NULL DEFAULT '',
    state        TEXT NOT NULL DEFAULT 'raw',      -- raw | stale | deleted (summaries add: summarized | failed, M3)
    content_hash TEXT NOT NULL,                    -- of (id, active swipe, text) of its messages
    participants TEXT NOT NULL DEFAULT '[]',
    whole        INTEGER NOT NULL DEFAULT 0,
    border_by    TEXT NOT NULL DEFAULT 'script',   -- script | model | hand
    reason       TEXT NOT NULL DEFAULT '',
    tries        INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
) STRICT;
CREATE INDEX p_memory_chapters_chat ON p_memory_chapters(chat_id, last_id);
