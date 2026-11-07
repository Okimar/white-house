"""The user's own skills (stage 10б): rows of the table `skills`."""

from __future__ import annotations

from typing import Any

from tavern.errors import NotFound
from tavern.storage.db import Database
from tavern.util.ids import new_id, now_iso

FIELDS = ("name", "title", "description", "body", "audience", "based_on", "enabled", "origin")


def _skill(row: Any) -> dict[str, Any]:
    out = dict(row)
    out["enabled"] = bool(out["enabled"])
    return out


class SkillRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    def list(self) -> list[dict[str, Any]]:
        return [_skill(r) for r in self.db.fetchall("SELECT * FROM skills ORDER BY name")]

    def get(self, skill_id: str) -> dict[str, Any]:
        row = self.db.fetchone("SELECT * FROM skills WHERE id = ?", (skill_id,))
        if row is None:
            raise NotFound("Инструкция не найдена")
        return _skill(row)

    def by_name(self, name: str) -> dict[str, Any] | None:
        row = self.db.fetchone("SELECT * FROM skills WHERE name = ?", (name,))
        return _skill(row) if row else None

    def create(self, fields: dict[str, Any]) -> dict[str, Any]:
        skill_id, now = new_id(), now_iso()
        values = {k: fields.get(k, "") for k in FIELDS}
        values["enabled"] = int(fields.get("enabled", True))
        values["audience"] = values["audience"] or "assistant"
        self.db.execute(
            f"INSERT INTO skills (id, {', '.join(FIELDS)}, created_at, updated_at) "
            f"VALUES (?, {', '.join('?' for _ in FIELDS)}, ?, ?)",
            (skill_id, *(values[k] for k in FIELDS), now, now))
        return self.get(skill_id)

    def update(self, skill_id: str, fields: dict[str, Any]) -> dict[str, Any]:
        self.get(skill_id)
        changes = {k: (int(v) if k == "enabled" else v) for k, v in fields.items() if k in FIELDS}
        if changes:
            sets = ", ".join(f"{k} = ?" for k in changes)
            self.db.execute(f"UPDATE skills SET {sets}, updated_at = ? WHERE id = ?",
                            (*changes.values(), now_iso(), skill_id))
        return self.get(skill_id)

    def delete(self, skill_id: str) -> None:
        self.get(skill_id)
        self.db.execute("DELETE FROM skills WHERE id = ?", (skill_id,))
