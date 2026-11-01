"""Chat members: which characters take part in a chat, with depth, importance and the "me" mark.

Member dict shape:
    chat_id, character_id, name, has_avatar, position, depth (None = start of the prompt),
    importance (1 most important .. 10), is_me, model_may_write,
    talkativeness (0..100), muted, blurb (stage 7: group chats)
"""

from __future__ import annotations

from typing import Any

from tavern.errors import Conflict, NotFound
from tavern.storage.db import Database
from tavern.util.ids import now_iso

DEFAULT_IMPORTANCE = 5
DEFAULT_TALKATIVENESS = 50
MEMBER_FIELDS = ("depth", "importance", "is_me", "model_may_write", "position", "talkativeness", "muted",
                 "blurb")

_SELECT = """
SELECT cm.chat_id, cm.character_id, cm.position, cm.depth, cm.importance, cm.is_me,
       cm.model_may_write, cm.talkativeness, cm.muted, cm.blurb, c.name, (c.avatar IS NOT NULL AND c.avatar != '') AS has_avatar
FROM chat_members cm JOIN characters c ON c.id = cm.character_id
"""


def _member(row: Any) -> dict[str, Any]:
    return {
        "chat_id": row["chat_id"],
        "character_id": row["character_id"],
        "name": row["name"],
        "has_avatar": bool(row["has_avatar"]),
        "position": row["position"],
        "depth": row["depth"],
        "importance": row["importance"],
        "is_me": bool(row["is_me"]),
        "model_may_write": bool(row["model_may_write"]),
        "talkativeness": row["talkativeness"],
        "muted": bool(row["muted"]),
        "blurb": row["blurb"],
    }


class MemberRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    def list(self, chat_id: str) -> list[dict[str, Any]]:
        rows = self.db.fetchall(
            _SELECT + " WHERE cm.chat_id = ? ORDER BY cm.position, c.name COLLATE NOCASE", (chat_id,)
        )
        return [_member(r) for r in rows]

    def get(self, chat_id: str, character_id: str) -> dict[str, Any]:
        row = self.db.fetchone(
            _SELECT + " WHERE cm.chat_id = ? AND cm.character_id = ?", (chat_id, character_id)
        )
        if row is None:
            raise NotFound(f"Character {character_id} is not in chat {chat_id}")
        return _member(row)

    def add(
        self,
        chat_id: str,
        character_id: str,
        *,
        depth: int | None = None,
        importance: int = DEFAULT_IMPORTANCE,
        is_me: bool = False,
        model_may_write: bool = True,
        talkativeness: int = DEFAULT_TALKATIVENESS,
        muted: bool = False,
        blurb: str = "",
    ) -> dict[str, Any]:
        with self.db.transaction() as conn:
            if conn.execute(
                "SELECT 1 FROM chat_members WHERE chat_id = ? AND character_id = ?",
                (chat_id, character_id),
            ).fetchone():
                raise Conflict("This character is already in the chat", code="already_member")
            position = conn.execute(
                "SELECT COALESCE(MAX(position), -1) + 1 FROM chat_members WHERE chat_id = ?",
                (chat_id,),
            ).fetchone()[0]
            if is_me:
                conn.execute("UPDATE chat_members SET is_me = 0 WHERE chat_id = ?", (chat_id,))
            conn.execute(
                "INSERT INTO chat_members (chat_id, character_id, position, depth, importance, "
                "is_me, model_may_write, talkativeness, muted, blurb) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (chat_id, character_id, position, depth, importance, int(is_me), int(model_may_write),
                 talkativeness, int(muted), blurb),
            )
            conn.execute("UPDATE chats SET updated_at = ? WHERE id = ?", (now_iso(), chat_id))
        return self.get(chat_id, character_id)

    def update(self, chat_id: str, character_id: str, changes: dict[str, Any]) -> dict[str, Any]:
        """Apply `changes` (keys from MEMBER_FIELDS). Marking a member "me" unmarks the others."""
        unknown = set(changes) - set(MEMBER_FIELDS)
        if unknown:
            raise ValueError(f"Unknown member fields: {sorted(unknown)}")
        self.get(chat_id, character_id)
        with self.db.transaction() as conn:
            if changes.get("is_me"):
                conn.execute("UPDATE chat_members SET is_me = 0 WHERE chat_id = ?", (chat_id,))
            for key, value in changes.items():
                stored = int(value) if isinstance(value, bool) else value
                conn.execute(
                    f"UPDATE chat_members SET {key} = ? WHERE chat_id = ? AND character_id = ?",
                    (stored, chat_id, character_id),
                )
        return self.get(chat_id, character_id)

    def remove(self, chat_id: str, character_id: str) -> None:
        self.get(chat_id, character_id)
        self.db.execute(
            "DELETE FROM chat_members WHERE chat_id = ? AND character_id = ?", (chat_id, character_id)
        )
