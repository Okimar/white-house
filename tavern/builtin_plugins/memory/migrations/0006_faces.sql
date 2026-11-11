-- Memory++ M6 (ADR 0019): mini-cards of side characters («Лица»): people without a card (an innkeeper, a guard) who
-- keep coming back. Names start with p_memory_ (plugins/schema.py checks it). 0001-0005 are shipped and never edited.
--
-- A face is APP-WIDE (not per chat): a favourite one (★) is used in every chat where it is named. Its card is flat
-- text of about 200 tokens (never a bubble tree). made_by: auto | hand. locked = a hand edit: automatic work never
-- changes it. deleted = removed by hand (it can be restored). merged_into = the face it was merged into (it stays only
-- for undo). character_id = the real character made from it («Сделать настоящим персонажем»); the face stays linked.
CREATE TABLE p_memory_faces (
    id             TEXT PRIMARY KEY,
    name           TEXT NOT NULL,
    aliases        TEXT NOT NULL DEFAULT '[]',
    card           TEXT NOT NULL DEFAULT '',
    favourite      INTEGER NOT NULL DEFAULT 0,
    made_by        TEXT NOT NULL,
    locked         INTEGER NOT NULL DEFAULT 0,
    deleted        INTEGER NOT NULL DEFAULT 0,
    merged_into    TEXT NOT NULL DEFAULT '',
    character_id   TEXT NOT NULL DEFAULT '',
    origin_chat_id TEXT NOT NULL DEFAULT '',     -- the chat it was found in first (no foreign key: a face outlives it)
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL
) STRICT;

-- Every state of a face's name, aliases and card, newest first: «Версии» and «Вернуть эту».
CREATE TABLE p_memory_face_versions (
    id         TEXT PRIMARY KEY,
    face_id    TEXT NOT NULL REFERENCES p_memory_faces(id) ON DELETE CASCADE,
    name       TEXT NOT NULL,
    aliases    TEXT NOT NULL DEFAULT '[]',
    card       TEXT NOT NULL,
    made_by    TEXT NOT NULL,
    created_at TEXT NOT NULL
) STRICT;
CREATE INDEX p_memory_face_versions_face ON p_memory_face_versions(face_id, created_at);

-- Where a face appeared (and its «now» there: the development layer of target 'face:<id>' in p_memory_texts, M4).
-- in_use = «Использовать в этом чате»; 0 = never sent in that chat, also as a favourite. No foreign key on the message
-- (as with chapters, 0002).
CREATE TABLE p_memory_face_chats (
    face_id          TEXT NOT NULL REFERENCES p_memory_faces(id) ON DELETE CASCADE,
    chat_id          TEXT NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
    first_message_id TEXT,
    in_use           INTEGER NOT NULL DEFAULT 1,
    created_at       TEXT NOT NULL,
    PRIMARY KEY (face_id, chat_id)
) STRICT;
CREATE INDEX p_memory_face_chats_chat ON p_memory_face_chats(chat_id);

-- Which closed chapters the faces step already read (one request per chapter, in story order), as for layers (0004).
CREATE TABLE p_memory_face_runs (
    chapter_id TEXT PRIMARY KEY REFERENCES p_memory_chapters(id) ON DELETE CASCADE,
    chat_id    TEXT NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
    state      TEXT NOT NULL,                 -- done | failed
    error      TEXT NOT NULL DEFAULT '',
    tries      INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
) STRICT;
CREATE INDEX p_memory_face_runs_chat ON p_memory_face_runs(chat_id);

-- A face's card has a vector per chat it is used in ('f:<face id>@<chat id>'); a face that goes away takes them along.
CREATE TRIGGER p_memory_faces_gone AFTER DELETE ON p_memory_faces BEGIN
    DELETE FROM p_memory_vectors WHERE item_id LIKE 'f:' || OLD.id || '@%';
END;
