"""Groups of characters, lorebooks and chats (tables `groups`, `group_members`).

Group dict shape: id, kind, name, position, count. An item is in at most one group of its kind;
no row in group_members means «Без группы». Deleting a group deletes only the membership rows.
"""

from __future__ import annotations

from typing import Any

from tavern.errors import NotFound
from tavern.storage.db import Database
from tavern.util.ids import new_id, now_iso

KINDS = {"character": "characters", "lorebook": "lorebooks", "chat": "chats"}


class GroupRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    def list(self, kind: str) -> list[dict[str, Any]]:
        rows = self.db.fetchall(
            "SELECT g.id, g.kind, g.name, g.position, "
            "(SELECT COUNT(*) FROM group_members m WHERE m.group_id = g.id) AS count "
            "FROM groups g WHERE g.kind = ? ORDER BY g.position, g.created_at, g.id", (kind,))
        return [dict(r) for r in rows]

    def members(self, kind: str) -> dict[str, str]:
        rows = self.db.fetchall("SELECT item_id, group_id FROM group_members WHERE kind = ?", (kind,))
        return {r["item_id"]: r["group_id"] for r in rows}

    def get(self, group_id: str) -> dict[str, Any]:
        row = self.db.fetchone(
            "SELECT g.id, g.kind, g.name, g.position, "
            "(SELECT COUNT(*) FROM group_members m WHERE m.group_id = g.id) AS count "
            "FROM groups g WHERE g.id = ?", (group_id,))
        if row is None:
            raise NotFound(f"Group {group_id} not found")
        return dict(row)

    def create(self, kind: str, name: str) -> dict[str, Any]:
        group_id = new_id()
        last = self.db.fetchone("SELECT COALESCE(MAX(position), 0) AS p FROM groups WHERE kind = ?", (kind,))
        self.db.execute("INSERT INTO groups (id, kind, name, position, created_at) VALUES (?, ?, ?, ?, ?)",
                        (group_id, kind, name, last["p"] + 1, now_iso()))
        return self.get(group_id)

    def rename(self, group_id: str, name: str) -> dict[str, Any]:
        self.get(group_id)
        self.db.execute("UPDATE groups SET name = ? WHERE id = ?", (name, group_id))
        return self.get(group_id)

    def delete(self, group_id: str) -> None:
        self.get(group_id)
        self.db.execute("DELETE FROM groups WHERE id = ?", (group_id,))

    def item_exists(self, kind: str, item_id: str) -> bool:
        table = KINDS[kind]  # fixed names, never user text
        return self.db.fetchone(f"SELECT 1 FROM {table} WHERE id = ?", (item_id,)) is not None

    def assign(self, kind: str, item_ids: list[str], group_id: str | None) -> None:
        with self.db.transaction() as conn:
            for item_id in item_ids:
                if group_id is None:
                    conn.execute("DELETE FROM group_members WHERE kind = ? AND item_id = ?", (kind, item_id))
                else:
                    conn.execute("INSERT INTO group_members (kind, item_id, group_id) VALUES (?, ?, ?) "
                                 "ON CONFLICT(kind, item_id) DO UPDATE SET group_id = excluded.group_id",
                                 (kind, item_id, group_id))
