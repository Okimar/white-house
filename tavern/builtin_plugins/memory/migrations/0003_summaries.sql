-- Memory++ M3 (ADR 0019): summaries of chapters, «Сюжет до сих пор», sticky hits, per-member flags.
-- Names start with p_memory_ (plugins/schema.py checks it). 0001 and 0002 are shipped and never edited.

-- Why a chapter has no summary (yet): the last failure, kept for the window and for the backoff of the retry.
ALTER TABLE p_memory_chapters ADD COLUMN error TEXT NOT NULL DEFAULT '';
ALTER TABLE p_memory_chapters ADD COLUMN failed_at TEXT NOT NULL DEFAULT '';

-- Every model- or hand-written text, versioned. kind: summary (target = chapter id) | plot (target = '').
--   summary: made_from = the chapter's content hash the text tells; exactly one row per chapter has is_current = 1,
--            older versions stay for «Версии» (is_current = 0). A deleted chapter keeps its rows.
--   plot: anchor_id = the last message of the last chapter it covers; the plot of a branch is the newest row whose
--         anchor is on the branch (so a branch keeps its own plot, ADR 3.1); made_from = JSON list of the summary
--         ids it was built from. No foreign key on anchor_id: a deleted anchor message just retires the version.
-- locked = a hand edit: automatic work never replaces it.
CREATE TABLE p_memory_texts (
    id         TEXT PRIMARY KEY,
    chat_id    TEXT NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
    kind       TEXT NOT NULL,
    target     TEXT NOT NULL,
    anchor_id  TEXT,
    made_from  TEXT NOT NULL DEFAULT '',
    text       TEXT NOT NULL,
    made_by    TEXT NOT NULL,
    locked     INTEGER NOT NULL DEFAULT 0,
    is_current INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
) STRICT;
CREATE INDEX p_memory_texts_target ON p_memory_texts(chat_id, kind, target, created_at);

-- Sticky: the reply number when a chapter last went into the prompt, and in which form (no flicker between replies).
CREATE TABLE p_memory_hits (
    chat_id    TEXT NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
    chapter_id TEXT NOT NULL REFERENCES p_memory_chapters(id) ON DELETE CASCADE,
    hit_gen    INTEGER NOT NULL,
    what       TEXT NOT NULL,
    PRIMARY KEY (chat_id, chapter_id)
) STRICT;

-- Per member of a chat: «Помнит всё» (every chapter, not only their own) and «Не наслаивать» (no development layer,
-- used from M4).
CREATE TABLE p_memory_members (
    chat_id       TEXT NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
    character_id  TEXT NOT NULL,
    remembers_all INTEGER NOT NULL DEFAULT 0,
    no_layer      INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (chat_id, character_id)
) STRICT;

-- A chapter that goes away takes the vector of its summary along (a piece's vectors go with the piece).
CREATE TRIGGER p_memory_chapters_gone AFTER DELETE ON p_memory_chapters BEGIN
    DELETE FROM p_memory_vectors WHERE item_id = 's:' || OLD.id;
END;
