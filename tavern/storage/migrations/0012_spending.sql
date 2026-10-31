-- Stage 6е: one row per model request, for spending per message, chat, day and month.
-- `day` is the local calendar day (YYYY-MM-DD) the request finished on; months are its prefix.
-- `cost` is in dollars, NULL when no price was known; `estimated` = the service reported no
-- token counts (or the reply was stopped) and the counts are a rough guess.
-- Rows are kept when a chat or connection is deleted: money spent stays spent.
CREATE TABLE spending (
    id                INTEGER PRIMARY KEY,
    at                TEXT NOT NULL,
    day               TEXT NOT NULL,
    chat_id           TEXT,
    message_id        TEXT,
    connection_id     TEXT NOT NULL DEFAULT '',
    connection_name   TEXT NOT NULL DEFAULT '',
    model             TEXT NOT NULL DEFAULT '',
    purpose           TEXT NOT NULL DEFAULT 'chat',
    prompt_tokens     INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    cost              REAL,
    estimated         INTEGER NOT NULL DEFAULT 0
) STRICT;

CREATE INDEX spending_day ON spending (day);
CREATE INDEX spending_chat ON spending (chat_id);
CREATE INDEX spending_message ON spending (message_id);
