"""Characters. `data` is the V2 card `data` object, kept whole so unknown fields round-trip."""

from __future__ import annotations

import json
from typing import Any

from tavern.errors import NotFound
from tavern.storage.db import Database
from tavern.util.ids import new_id, now_iso


def _full(row: Any) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "spec": row["spec"],
        "data": json.loads(row["data"]),
        "avatar": row["avatar"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


class CharacterRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    def create(self, data: dict[str, Any], *, spec: str = "chara_card_v2", avatar: str | None = None) -> dict:
        char_id, now = new_id(), now_iso()
        self.db.execute(
            "INSERT INTO characters (id, name, spec, data, avatar, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (char_id, data["name"], spec, json.dumps(data, ensure_ascii=False), avatar, now, now),
        )
        return self.get(char_id)

    def get(self, char_id: str) -> dict[str, Any]:
        row = self.db.fetchone("SELECT * FROM characters WHERE id = ?", (char_id,))
        if row is None:
            raise NotFound(f"Character {char_id} not found")
        return _full(row)

    def list(self) -> list[dict[str, Any]]:
        """Summaries without the (large) card data."""
        rows = self.db.fetchall(
            "SELECT id, name, spec, avatar, created_at, updated_at FROM characters "
            "ORDER BY name COLLATE NOCASE"
        )
        return [dict(r) for r in rows]

    def update(self, char_id: str, data: dict[str, Any]) -> dict[str, Any]:
        self.get(char_id)
        self.db.execute(
            "UPDATE characters SET name = ?, data = ?, updated_at = ? WHERE id = ?",
            (data["name"], json.dumps(data, ensure_ascii=False), now_iso(), char_id),
        )
        return self.get(char_id)

    def delete(self, char_id: str) -> None:
        self.get(char_id)
        self.db.execute("DELETE FROM characters WHERE id = ?", (char_id,))
