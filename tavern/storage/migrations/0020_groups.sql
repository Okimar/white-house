-- Groups: a free-text name that gathers characters, lorebooks or chats (each kind has its own groups).
-- An item is in one group or none; deleting a group frees its items, deleting an item frees its place.
CREATE TABLE groups (
    id         TEXT PRIMARY KEY,
    kind       TEXT NOT NULL CHECK (kind IN ('character', 'lorebook', 'chat')),
    name       TEXT NOT NULL,
    position   INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
) STRICT;
CREATE INDEX idx_groups_kind ON groups(kind, position);

CREATE TABLE group_members (
    kind     TEXT NOT NULL,
    item_id  TEXT NOT NULL,
    group_id TEXT NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
    PRIMARY KEY (kind, item_id)
) STRICT;
CREATE INDEX idx_group_members_group ON group_members(group_id);

CREATE TRIGGER groups_character_deleted AFTER DELETE ON characters
BEGIN DELETE FROM group_members WHERE kind = 'character' AND item_id = OLD.id; END;
CREATE TRIGGER groups_lorebook_deleted AFTER DELETE ON lorebooks
BEGIN DELETE FROM group_members WHERE kind = 'lorebook' AND item_id = OLD.id; END;
CREATE TRIGGER groups_chat_deleted AFTER DELETE ON chats
BEGIN DELETE FROM group_members WHERE kind = 'chat' AND item_id = OLD.id; END;
