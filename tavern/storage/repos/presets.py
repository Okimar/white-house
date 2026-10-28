"""Prompt presets as rows of (id, name, data JSON). Validation lives in core/presets.py."""

from __future__ import annotations

import json
from typing import Any

from tavern.errors import NotFound
from tavern.storage.db import Database
from tavern.util.ids import new_id, now_iso


def _preset(row: Any) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "data": json.loads(row["data"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


class PresetRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    def list(self) -> list[dict[str, Any]]:
        rows = self.db.fetchall(
            "SELECT id, name, created_at, updated_at FROM presets ORDER BY name COLLATE NOCASE"
        )
        return [dict(r) for r in rows]

    def get(self, preset_id: str) -> dict[str, Any]:
        row = self.db.fetchone("SELECT * FROM presets WHERE id = ?", (preset_id,))
        if row is None:
            raise NotFound(f"Preset {preset_id} not found")
        return _preset(row)

    def create(self, name: str, data: dict[str, Any]) -> dict[str, Any]:
        preset_id, now = new_id(), now_iso()
        self.db.execute(
            "INSERT INTO presets (id, name, data, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            (preset_id, name, json.dumps(data, ensure_ascii=False), now, now),
        )
        return self.get(preset_id)

    def update(self, preset_id: str, name: str, data: dict[str, Any]) -> dict[str, Any]:
        self.get(preset_id)
        self.db.execute(
            "UPDATE presets SET name = ?, data = ?, updated_at = ? WHERE id = ?",
            (name, json.dumps(data, ensure_ascii=False), now_iso(), preset_id),
        )
        return self.get(preset_id)

    def delete(self, preset_id: str) -> None:
        self.get(preset_id)
        self.db.execute("DELETE FROM presets WHERE id = ?", (preset_id,))
