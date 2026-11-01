-- Stage 8а: plugins. One row per plugin Tavern has seen; the files live in data/plugins/<id>/
-- (built-in ones in tavern/builtin_plugins/<id>/). A plugin without a row is new and off.
-- approved_hash: sha256 of the plugin's files when the user turned it on. Different files (an
--   update, a hand edit) switch it off until the user approves them again. Built-ins need no approval.
-- source: where it came from, {"kind": "folder"|"file"|"github", "url", "ref", "sha"}.
-- settings: the plugin's own settings (fields declared in its plugin.json).
-- access: model use, {"models": bool, "ask": bool, "day": $, "month": $, "per_reply": n}.
CREATE TABLE plugins (
    id            TEXT PRIMARY KEY,
    enabled       INTEGER NOT NULL DEFAULT 0,
    approved_hash TEXT NOT NULL DEFAULT '',
    source        TEXT NOT NULL DEFAULT '{}',
    settings      TEXT NOT NULL DEFAULT '{}',
    access        TEXT NOT NULL DEFAULT '{}',
    installed_at  TEXT NOT NULL,
    updated_at    TEXT NOT NULL
) STRICT;

-- A plugin's own data, per chat (chat_id) or for the whole app (chat_id = '').
-- Values are JSON. Rows of a chat go with the chat; a plugin's rows go when the user deletes the
-- plugin and ticks "delete its data".
CREATE TABLE plugin_data (
    plugin_id  TEXT NOT NULL,
    chat_id    TEXT NOT NULL DEFAULT '',
    key        TEXT NOT NULL,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (plugin_id, chat_id, key)
) STRICT;

CREATE INDEX idx_plugin_data_chat ON plugin_data(chat_id);

CREATE TRIGGER plugin_data_chat_deleted AFTER DELETE ON chats BEGIN
    DELETE FROM plugin_data WHERE chat_id = OLD.id;
END;
