-- Stage 11: regex rules (scripts that change text on the screen, in what the model gets, or in the saved
-- message). scope: 'global' (owner_id empty), 'character' (owner_id = the character) or 'preset' (owner_id
-- = the preset). approved = 0 for rules that arrived with an imported card or preset: they do nothing until
-- the person allows them once. `extra` keeps the fields of a SillyTavern script that we do not use, so an
-- export gives them back. Rules of a deleted character or preset go with it (triggers below).
CREATE TABLE regex_scripts (
    id           TEXT PRIMARY KEY,
    scope        TEXT NOT NULL CHECK (scope IN ('global', 'character', 'preset')),
    owner_id     TEXT NOT NULL DEFAULT '',
    name         TEXT NOT NULL DEFAULT '',
    enabled      INTEGER NOT NULL DEFAULT 1,
    find         TEXT NOT NULL DEFAULT '',
    replace_with TEXT NOT NULL DEFAULT '',
    trim_out     TEXT NOT NULL DEFAULT '[]',
    places       TEXT NOT NULL DEFAULT '["user","assistant"]',
    applies_to   TEXT NOT NULL DEFAULT 'display' CHECK (applies_to IN ('display', 'prompt', 'both', 'stored')),
    min_depth    INTEGER,
    max_depth    INTEGER,
    run_on_edit  INTEGER NOT NULL DEFAULT 0,
    macros       TEXT NOT NULL DEFAULT 'none' CHECK (macros IN ('none', 'raw', 'escaped')),
    render_html  INTEGER NOT NULL DEFAULT 0,
    approved     INTEGER NOT NULL DEFAULT 1,
    position     INTEGER NOT NULL DEFAULT 0,
    extra        TEXT NOT NULL DEFAULT '{}',
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
) STRICT;
CREATE INDEX idx_regex_owner ON regex_scripts(scope, owner_id, position);

CREATE TRIGGER regex_character_deleted AFTER DELETE ON characters
BEGIN DELETE FROM regex_scripts WHERE scope = 'character' AND owner_id = OLD.id; END;
CREATE TRIGGER regex_preset_deleted AFTER DELETE ON presets
BEGIN DELETE FROM regex_scripts WHERE scope = 'preset' AND owner_id = OLD.id; END;
