"""The user's instruct templates (built-in ones are in core/templates.py, never stored)."""

from __future__ import annotations

import json
from typing import Any

from tavern.errors import NotFound
from tavern.storage.db import Database
from tavern.util.ids import new_id, now_iso


def _row(row: Any) -> dict[str, Any]:
    return {"id": row["id"], "builtin": False, **json.loads(row["data"]), "name": row["name"],
            "updated_at": row["updated_at"]}


class TemplateRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    def list(self) -> list[dict[str, Any]]:
        return [_row(r) for r in self.db.fetchall("SELECT * FROM instruct_templates ORDER BY name COLLATE NOCASE")]

    def get(self, template_id: str) -> dict[str, Any]:
        row = self.db.fetchone("SELECT * FROM instruct_templates WHERE id = ?", (template_id,))
        if row is None:
            raise NotFound(f"Template {template_id} not found")
        return _row(row)

    def create(self, data: dict[str, Any]) -> dict[str, Any]:
        template_id, now = new_id(), now_iso()
        self.db.execute(
            "INSERT INTO instruct_templates (id, name, data, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            (template_id, data["name"], json.dumps(data), now, now),
        )
        return self.get(template_id)

    def update(self, template_id: str, data: dict[str, Any]) -> dict[str, Any]:
        self.get(template_id)
        self.db.execute(
            "UPDATE instruct_templates SET name = ?, data = ?, updated_at = ? WHERE id = ?",
            (data["name"], json.dumps(data), now_iso(), template_id),
        )
        return self.get(template_id)

    def delete(self, template_id: str) -> None:
        self.get(template_id)
        self.db.execute("DELETE FROM instruct_templates WHERE id = ?", (template_id,))
