"""Plugins (on/off, approval, source, settings, model access) and plugin_data (their own storage)."""

from __future__ import annotations

import json
from typing import Any

from tavern.storage.db import Database
from tavern.util.ids import now_iso

_JSON = ("source", "settings", "access")


def _row(row: Any) -> dict[str, Any]:
    out = dict(row)
    out["enabled"] = bool(out["enabled"])
    for key in _JSON:
        out[key] = json.loads(out[key])
    return out


class PluginRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    def get(self, plugin_id: str) -> dict[str, Any] | None:
        row = self.db.fetchone("SELECT * FROM plugins WHERE id = ?", (plugin_id,))
        return _row(row) if row else None

    def all(self) -> dict[str, dict[str, Any]]:
        return {r["id"]: _row(r) for r in self.db.fetchall("SELECT * FROM plugins")}

    def save(self, plugin_id: str, **fields: Any) -> dict[str, Any]:
        """Create the row if missing, then set `fields` (enabled, approved_hash, source, settings, access)."""
        now = now_iso()
        self.db.execute(
            "INSERT INTO plugins (id, installed_at, updated_at) VALUES (?, ?, ?) ON CONFLICT(id) DO NOTHING",
            (plugin_id, now, now))
        if fields:
            values = [json.dumps(v, ensure_ascii=False) if k in _JSON else (int(v) if k == "enabled" else v)
                      for k, v in fields.items()]
            sets = ", ".join(f"{k} = ?" for k in fields)
            self.db.execute(f"UPDATE plugins SET {sets}, updated_at = ? WHERE id = ?", (*values, now, plugin_id))
        return self.get(plugin_id) or {}

    def delete(self, plugin_id: str, *, data: bool) -> None:
        with self.db.transaction() as conn:
            conn.execute("DELETE FROM plugins WHERE id = ?", (plugin_id,))
            if data:
                conn.execute("DELETE FROM plugin_data WHERE plugin_id = ?", (plugin_id,))

    # -- plugin_data -------------------------------------------------------------------------

    def data_get(self, plugin_id: str, chat_id: str, key: str, default: Any = None) -> Any:
        row = self.db.fetchone(
            "SELECT value FROM plugin_data WHERE plugin_id = ? AND chat_id = ? AND key = ?",
            (plugin_id, chat_id, key))
        return default if row is None else json.loads(row["value"])

    def data_set(self, plugin_id: str, chat_id: str, key: str, value: Any) -> None:
        self.db.execute(
            "INSERT INTO plugin_data (plugin_id, chat_id, key, value, updated_at) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(plugin_id, chat_id, key) DO UPDATE SET value = excluded.value, "
            "updated_at = excluded.updated_at",
            (plugin_id, chat_id, key, json.dumps(value, ensure_ascii=False), now_iso()))

    def data_delete(self, plugin_id: str, chat_id: str, key: str) -> None:
        self.db.execute("DELETE FROM plugin_data WHERE plugin_id = ? AND chat_id = ? AND key = ?",
                        (plugin_id, chat_id, key))

    def data_all(self, plugin_id: str, chat_id: str) -> dict[str, Any]:
        rows = self.db.fetchall(
            "SELECT key, value FROM plugin_data WHERE plugin_id = ? AND chat_id = ? ORDER BY key",
            (plugin_id, chat_id))
        return {r["key"]: json.loads(r["value"]) for r in rows}

    def data_count(self, plugin_id: str) -> int:
        row = self.db.fetchone("SELECT COUNT(*) AS n FROM plugin_data WHERE plugin_id = ?", (plugin_id,))
        return int(row["n"])
