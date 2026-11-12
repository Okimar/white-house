-- Empty places in a chat's cast: a character of an imported chat that this install does not have (or
-- that the owner chose not to link by name). The place keeps the original name and the same settings a
-- member has; it has no card, never speaks and is not shown to the model. "Заменить" turns it into a
-- real chat_members row (the settings move with it). `chat_members` stays as it is: a real member
-- always has a character, and the code that reads members does not see these places unless asked.
CREATE TABLE chat_member_slots (
    chat_id         TEXT NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
    slot_id         TEXT NOT NULL,
    name            TEXT NOT NULL,
    position        INTEGER NOT NULL DEFAULT 0,
    depth           INTEGER CHECK (depth IS NULL OR depth BETWEEN 0 AND 1000),
    importance      INTEGER NOT NULL DEFAULT 5 CHECK (importance BETWEEN 1 AND 10),
    model_may_write INTEGER NOT NULL DEFAULT 1,
    talkativeness   INTEGER NOT NULL DEFAULT 50 CHECK (talkativeness BETWEEN 0 AND 100),
    muted           INTEGER NOT NULL DEFAULT 0,
    blurb           TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (chat_id, slot_id)
) STRICT;
