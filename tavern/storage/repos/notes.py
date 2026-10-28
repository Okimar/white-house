"""Author's notes (global, a character's, a chat's) and the notes attached to messages.

Note dict shape:
    id, scope ('global'|'character'|'chat'), character_id, chat_id, position, title, text,
    enabled, depth, role, interval (replies skipped between sendings), mode ('once'|'hidden'|'visible'),
    created_at, updated_at
Attached note dict shape (a note kept in the history after a message):
    id, chat_id, message_id, kind ('hidden'|'visible'), note_id (None = written by hand), title, role,
    text, created_at
"""

from __future__ import annotations

from typing import Any

from tavern.errors import NotFound
from tavern.storage.db import Database
from tavern.util.ids import new_id, now_iso

NOTE_FIELDS = ("position", "title", "text", "enabled", "depth", "role", "interval", "mode")


def _note(row: Any) -> dict[str, Any]:
    note = dict(row)
    note["enabled"] = bool(note["enabled"])
    return note


class NoteRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    # --- notes ----------------------------------------------------------------------------------

    def list(self, scope: str, owner_id: str | None = None) -> list[dict[str, Any]]:
        column = {"global": None, "character": "character_id", "chat": "chat_id"}[scope]
        where, params = "scope = ?", [scope]
        if column:
            where += f" AND {column} = ?"
            params.append(owner_id)
        rows = self.db.fetchall(
            f"SELECT * FROM author_notes WHERE {where} ORDER BY position, created_at", params
        )
        return [_note(r) for r in rows]

    def for_chat(self, chat_id: str, character_ids: list[str]) -> list[dict[str, Any]]:
        """Every note that applies to a chat: global, its members', its own; in that order."""
        marks = ",".join("?" * len(character_ids)) or "NULL"
        rows = self.db.fetchall(
            f"""SELECT * FROM author_notes
                WHERE scope = 'global' OR chat_id = ? OR character_id IN ({marks})
                ORDER BY CASE scope WHEN 'global' THEN 0 WHEN 'character' THEN 1 ELSE 2 END,
                         position, created_at""",
            [chat_id, *character_ids],
        )
        return [_note(r) for r in rows]

    def get(self, note_id: str) -> dict[str, Any]:
        row = self.db.fetchone("SELECT * FROM author_notes WHERE id = ?", (note_id,))
        if row is None:
            raise NotFound(f"Note {note_id} not found")
        return _note(row)

    def create(self, scope: str, owner_id: str | None, fields: dict[str, Any]) -> dict[str, Any]:
        note_id, now = new_id(), now_iso()
        values = {k: fields[k] for k in NOTE_FIELDS if k in fields}
        if "position" not in values:
            values["position"] = len(self.list(scope, owner_id))
        if "enabled" in values:
            values["enabled"] = int(values["enabled"])
        columns = ["id", "scope", "character_id", "chat_id", "created_at", "updated_at", *values]
        row = [note_id, scope, owner_id if scope == "character" else None,
               owner_id if scope == "chat" else None, now, now, *values.values()]
        self.db.execute(
            f"INSERT INTO author_notes ({', '.join(columns)}) VALUES ({', '.join('?' * len(row))})", row
        )
        return self.get(note_id)

    def update(self, note_id: str, fields: dict[str, Any]) -> dict[str, Any]:
        self.get(note_id)
        values = {k: fields[k] for k in NOTE_FIELDS if k in fields}
        if "enabled" in values:
            values["enabled"] = int(values["enabled"])
        if values:
            sets = ", ".join(f"{k} = ?" for k in values)
            self.db.execute(f"UPDATE author_notes SET {sets}, updated_at = ? WHERE id = ?",
                            (*values.values(), now_iso(), note_id))
        return self.get(note_id)

    def delete(self, note_id: str) -> None:
        self.get(note_id)
        self.db.execute("DELETE FROM author_notes WHERE id = ?", (note_id,))

    # --- notes attached to messages -------------------------------------------------------------

    def attached(self, chat_id: str, kinds: tuple[str, ...] = ("hidden", "visible")) -> list[dict[str, Any]]:
        marks = ",".join("?" * len(kinds))
        rows = self.db.fetchall(
            f"SELECT * FROM message_notes WHERE chat_id = ? AND kind IN ({marks}) ORDER BY created_at, id",
            (chat_id, *kinds),
        )
        return [dict(r) for r in rows]

    def get_attached(self, attached_id: str) -> dict[str, Any]:
        row = self.db.fetchone("SELECT * FROM message_notes WHERE id = ?", (attached_id,))
        if row is None:
            raise NotFound(f"Message note {attached_id} not found")
        return dict(row)

    def attach(self, chat_id: str, message_id: str, *, kind: str, text: str, role: str = "system",
               title: str = "", note_id: str | None = None) -> dict[str, Any]:
        """Attach a note after a message. The same note on the same message is updated, not doubled
        (a regenerated reply fires its notes again at the same place)."""
        if note_id is not None:
            row = self.db.fetchone(
                "SELECT id FROM message_notes WHERE message_id = ? AND note_id = ?", (message_id, note_id)
            )
            if row is not None:
                self.db.execute("UPDATE message_notes SET kind = ?, text = ?, role = ?, title = ? WHERE id = ?",
                                (kind, text, role, title, row["id"]))
                return self.get_attached(row["id"])
        attached_id = new_id()
        self.db.execute(
            """INSERT INTO message_notes (id, chat_id, message_id, kind, note_id, title, role, text, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (attached_id, chat_id, message_id, kind, note_id, title, role, text, now_iso()),
        )
        return self.get_attached(attached_id)

    def update_attached(self, attached_id: str, fields: dict[str, Any]) -> dict[str, Any]:
        self.get_attached(attached_id)
        values = {k: fields[k] for k in ("text", "role", "title", "kind") if k in fields}
        if values:
            sets = ", ".join(f"{k} = ?" for k in values)
            self.db.execute(f"UPDATE message_notes SET {sets} WHERE id = ?", (*values.values(), attached_id))
        return self.get_attached(attached_id)

    def detach(self, attached_ids: list[str]) -> int:
        if not attached_ids:
            return 0
        marks = ",".join("?" * len(attached_ids))
        return self.db.execute(f"DELETE FROM message_notes WHERE id IN ({marks})", attached_ids).rowcount

    def detach_from(self, chat_id: str, message_ids: list[str]) -> int:
        if not message_ids:
            return 0
        marks = ",".join("?" * len(message_ids))
        return self.db.execute(
            f"DELETE FROM message_notes WHERE chat_id = ? AND message_id IN ({marks})",
            (chat_id, *message_ids),
        ).rowcount

    def set_kind(self, chat_id: str, message_ids: list[str], kind: str) -> int:
        if not message_ids:
            return 0
        marks = ",".join("?" * len(message_ids))
        return self.db.execute(
            f"UPDATE message_notes SET kind = ? WHERE chat_id = ? AND message_id IN ({marks})",
            (kind, chat_id, *message_ids),
        ).rowcount

    def clear(self, chat_id: str, kind: str) -> int:
        return self.db.execute("DELETE FROM message_notes WHERE chat_id = ? AND kind = ?",
                               (chat_id, kind)).rowcount
