"""Lorebooks and where they are switched on (global, a chat, a character).

Book dict shape: id, name, description, settings {}, extra {}, created_at, updated_at, and in
lists also `blocks` (how many bubbles it has).
Use dict shape: lorebook_id, scope ('global'|'chat'|'character'), chat_id, character_id, position.
"""

from __future__ import annotations

import json
from typing import Any

from tavern.errors import NotFound
from tavern.storage.db import Database
from tavern.util.ids import new_id, now_iso

BOOK_FIELDS = ("name", "description", "settings", "extra")
JSON_FIELDS = ("settings", "extra")
TARGET = {"global": None, "chat": "chat_id", "character": "character_id"}


def _book(row: Any) -> dict[str, Any]:
    book = dict(row)
    for key in JSON_FIELDS:
        book[key] = json.loads(book[key])
    return book


class LorebookRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    def list(self) -> list[dict[str, Any]]:
        rows = self.db.fetchall(
            "SELECT l.*, (SELECT COUNT(*) FROM character_blocks b WHERE b.lorebook_id = l.id) AS blocks "
            "FROM lorebooks l ORDER BY l.name COLLATE NOCASE, l.created_at"
        )
        return [_book(r) for r in rows]

    def get(self, book_id: str) -> dict[str, Any]:
        row = self.db.fetchone("SELECT * FROM lorebooks WHERE id = ?", (book_id,))
        if row is None:
            raise NotFound(f"Lorebook {book_id} not found")
        return _book(row)

    def exists(self, book_id: str) -> bool:
        return self.db.fetchone("SELECT 1 FROM lorebooks WHERE id = ?", (book_id,)) is not None

    def create(self, fields: dict[str, Any]) -> dict[str, Any]:
        book_id, now = new_id(), now_iso()
        values = {k: fields[k] for k in BOOK_FIELDS if k in fields}
        for key in JSON_FIELDS:
            if key in values:
                values[key] = json.dumps(values[key], ensure_ascii=False)
        columns = ["id", *values, "created_at", "updated_at"]
        self.db.execute(
            f"INSERT INTO lorebooks ({', '.join(columns)}) VALUES ({', '.join('?' * len(columns))})",
            (book_id, *values.values(), now, now),
        )
        return self.get(book_id)

    def update(self, book_id: str, changes: dict[str, Any]) -> dict[str, Any]:
        self.get(book_id)
        values = {k: changes[k] for k in BOOK_FIELDS if k in changes}
        if values:
            for key in JSON_FIELDS:
                if key in values:
                    values[key] = json.dumps(values[key], ensure_ascii=False)
            sets = ", ".join(f"{k} = ?" for k in values)
            self.db.execute(f"UPDATE lorebooks SET {sets}, updated_at = ? WHERE id = ?",
                            (*values.values(), now_iso(), book_id))
        return self.get(book_id)

    def delete(self, book_id: str) -> None:
        self.get(book_id)
        self.db.execute("DELETE FROM lorebooks WHERE id = ?", (book_id,))

    def find_by_name(self, name: str) -> dict[str, Any] | None:
        # SQLite's NOCASE folds only Latin letters, so "эйра" is matched to "Эйра" here instead.
        wanted = name.casefold()
        for row in self.db.fetchall("SELECT * FROM lorebooks ORDER BY created_at"):
            if row["name"].casefold() == wanted:
                return _book(row)
        return None

    # --- where books are switched on ---------------------------------------------------------

    def uses(self, scope: str, target: str | None = None) -> list[str]:
        """Book ids switched on for one place, in their order."""
        column = TARGET[scope]
        if column is None:
            rows = self.db.fetchall(
                "SELECT lorebook_id FROM lorebook_uses WHERE scope = 'global' ORDER BY position, id")
        else:
            rows = self.db.fetchall(
                f"SELECT lorebook_id FROM lorebook_uses WHERE scope = ? AND {column} = ? ORDER BY position, id",
                (scope, target))
        return [r["lorebook_id"] for r in rows]

    def set_uses(self, scope: str, target: str | None, book_ids: list[str]) -> None:
        """Replace the books of one place with `book_ids`, in that order."""
        column = TARGET[scope]
        with self.db.transaction() as conn:
            if column is None:
                conn.execute("DELETE FROM lorebook_uses WHERE scope = 'global'")
            else:
                conn.execute(f"DELETE FROM lorebook_uses WHERE scope = ? AND {column} = ?", (scope, target))
            for position, book_id in enumerate(book_ids):
                conn.execute(
                    "INSERT INTO lorebook_uses (lorebook_id, scope, chat_id, character_id, position) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (book_id, scope, target if scope == "chat" else None,
                     target if scope == "character" else None, position),
                )

    def places_of(self, book_id: str) -> list[dict[str, Any]]:
        rows = self.db.fetchall(
            "SELECT lorebook_id, scope, chat_id, character_id, position FROM lorebook_uses "
            "WHERE lorebook_id = ? ORDER BY scope, position", (book_id,))
        return [dict(r) for r in rows]
