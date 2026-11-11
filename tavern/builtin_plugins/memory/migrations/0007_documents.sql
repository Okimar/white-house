-- Memory++ M8 (ADR 0019): documents (a book, a long text) given to the memory. Names start with p_memory_
-- (plugins/schema.py checks it). 0001-0006 are shipped and never edited.
--
-- A document is cut into chapters (by headings, else by the chat cutter), the chapters into pieces, like an old chat;
-- pieces get vectors, chapters get summaries, and the chats it is attached to can find them. App-wide (one document
-- may be attached to many chats); NOT part of a chat file.
-- state: cut (chapters and pieces exist, no model work yet) | working | ready | failed.
-- started = the user saw the price and agreed: model work (vectors, summaries) may run.
CREATE TABLE p_memory_docs (
    id             TEXT PRIMARY KEY,
    title          TEXT NOT NULL,
    source_name    TEXT NOT NULL DEFAULT '',
    state          TEXT NOT NULL DEFAULT 'cut',
    started        INTEGER NOT NULL DEFAULT 0,
    with_summaries INTEGER NOT NULL DEFAULT 1,
    error          TEXT NOT NULL DEFAULT '',
    tokens         INTEGER NOT NULL DEFAULT 0,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL
) STRICT;

-- The whole text, kept once (chapters and the whole-chapter form read slices of it).
CREATE TABLE p_memory_doc_texts (
    doc_id TEXT PRIMARY KEY REFERENCES p_memory_docs(id) ON DELETE CASCADE,
    text   TEXT NOT NULL
) STRICT;

-- A chapter is a slice [at_start, at_end) of the text. whole = send the full text when it is found.
CREATE TABLE p_memory_doc_chapters (
    id       TEXT PRIMARY KEY,
    doc_id   TEXT NOT NULL REFERENCES p_memory_docs(id) ON DELETE CASCADE,
    number   INTEGER NOT NULL,
    title    TEXT NOT NULL DEFAULT '',
    at_start INTEGER NOT NULL,
    at_end   INTEGER NOT NULL,
    tokens   INTEGER NOT NULL,
    cut_by   TEXT NOT NULL,                   -- heading | cutter
    whole    INTEGER NOT NULL DEFAULT 0,
    error    TEXT NOT NULL DEFAULT '',
    tries    INTEGER NOT NULL DEFAULT 0
) STRICT;
CREATE INDEX p_memory_doc_chapters_doc ON p_memory_doc_chapters(doc_id, number);

-- Pieces keep their text (a piece is a cut of sentences, not always a slice of the original).
CREATE TABLE p_memory_doc_pieces (
    id         TEXT PRIMARY KEY,
    doc_id     TEXT NOT NULL REFERENCES p_memory_docs(id) ON DELETE CASCADE,
    chapter_id TEXT NOT NULL REFERENCES p_memory_doc_chapters(id) ON DELETE CASCADE,
    part       INTEGER NOT NULL,
    text       TEXT NOT NULL,
    text_hash  TEXT NOT NULL,
    tokens     INTEGER NOT NULL,
    keywords   TEXT NOT NULL DEFAULT '[]'
) STRICT;
CREATE INDEX p_memory_doc_pieces_doc ON p_memory_doc_pieces(doc_id, chapter_id, part);

CREATE TABLE p_memory_doc_summaries (
    chapter_id TEXT PRIMARY KEY REFERENCES p_memory_doc_chapters(id) ON DELETE CASCADE,
    doc_id     TEXT NOT NULL REFERENCES p_memory_docs(id) ON DELETE CASCADE,
    text       TEXT NOT NULL,
    made_by    TEXT NOT NULL,
    created_at TEXT NOT NULL
) STRICT;

-- Which chats may find the document.
CREATE TABLE p_memory_doc_chats (
    chat_id    TEXT NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
    doc_id     TEXT NOT NULL REFERENCES p_memory_docs(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    PRIMARY KEY (chat_id, doc_id)
) STRICT;
CREATE INDEX p_memory_doc_chats_doc ON p_memory_doc_chats(doc_id);

-- Vectors of pieces ('p:<piece id>') and summaries ('s:<chapter id>'): never exported, never journaled (the name ends
-- in _vectors).
CREATE TABLE p_memory_doc_vectors (
    item_id   TEXT NOT NULL,
    doc_id    TEXT NOT NULL REFERENCES p_memory_docs(id) ON DELETE CASCADE,
    model     TEXT NOT NULL,
    text_hash TEXT NOT NULL,
    vector    BLOB NOT NULL,
    PRIMARY KEY (item_id, model)
) STRICT;
CREATE INDEX p_memory_doc_vectors_doc ON p_memory_doc_vectors(doc_id, model);
