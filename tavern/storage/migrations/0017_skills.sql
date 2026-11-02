-- Stage 10б: the user's own skills (instructions the assistant reads). Built-in skills are files in
-- tavern/skills/ (read-only); a copy of one (based_on = its name) replaces it for the assistant.
-- audience: assistant | programmer | both. Turning built-in ones off is the setting "skills_off".
CREATE TABLE skills (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    title       TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    body        TEXT NOT NULL DEFAULT '',
    audience    TEXT NOT NULL DEFAULT 'assistant' CHECK (audience IN ('assistant', 'programmer', 'both')),
    based_on    TEXT NOT NULL DEFAULT '',
    enabled     INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
) STRICT;
