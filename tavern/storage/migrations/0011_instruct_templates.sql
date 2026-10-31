-- Stage 6в: the user's own instruct templates (built-in ones live in tavern/core/templates.py).
-- `data` is the template JSON in Tavern's own shape (core/templates.validate).
CREATE TABLE instruct_templates (
    id         TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    data       TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
) STRICT;
