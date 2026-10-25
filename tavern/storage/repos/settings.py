"""Key/value settings stored as JSON in the `settings` table."""

from __future__ import annotations

import json
from typing import Any

from tavern.storage.db import Database
from tavern.util.ids import now_iso


class SettingsRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    def get(self, key: str, default: Any = None) -> Any:
        row = self.db.fetchone("SELECT value FROM settings WHERE key = ?", (key,))
        return default if row is None else json.loads(row["value"])

    def all(self) -> dict[str, Any]:
        return {r["key"]: json.loads(r["value"]) for r in self.db.fetchall("SELECT * FROM settings")}

    def set(self, key: str, value: Any) -> None:
        self.db.execute(
            "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
            (key, json.dumps(value, ensure_ascii=False), now_iso()),
        )

    def delete(self, key: str) -> None:
        self.db.execute("DELETE FROM settings WHERE key = ?", (key,))
