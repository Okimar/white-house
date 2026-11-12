"""Chat members: which characters take part in a chat, with depth, importance and the "me" mark.

Member dict shape:
    chat_id, character_id, name, has_avatar, position, depth (None = start of the prompt),
    importance (1 most important .. 10), is_me, model_may_write,
    talkativeness (0..100), muted, blurb (stage 7: group chats),
    slot_id (the key the API uses: the character id, or the id of an empty place),
    empty (an empty place: `character_id` is None, the name is the one from the imported file, no card).

Empty places live in their own table (`chat_member_slots`). `list()` leaves them out unless asked, so the
speakers, the prompt, the memory plugin and everything else never meet a member without a character.
"""

from __future__ import annotations

from typing import Any

from tavern.errors import Conflict, NotFound
from tavern.storage.db import Database
from tavern.util.ids import new_id, now_iso

DEFAULT_IMPORTANCE = 5
DEFAULT_TALKATIVENESS = 50
MEMBER_FIELDS = ("depth", "importance", "is_me", "model_may_write", "position", "talkativeness", "muted",
                 "blurb")

_SELECT = """
SELECT cm.chat_id, cm.character_id, cm.position, cm.depth, cm.importance, cm.is_me,
       cm.model_may_write, cm.talkativeness, cm.muted, cm.blurb, c.name, (c.avatar IS NOT NULL AND c.avatar != '') AS has_avatar
FROM chat_members cm JOIN characters c ON c.id = cm.character_id
"""
_SLOT_SELECT = """
SELECT chat_id, slot_id, name, position, depth, importance, model_may_write, talkativeness, muted, blurb
FROM chat_member_slots
"""


def _member(row: Any) -> dict[str, Any]:
    return {
        "chat_id": row["chat_id"],
        "character_id": row["character_id"],
        "slot_id": row["character_id"],
        "empty": False,
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


def _slot(row: Any) -> dict[str, Any]:
    return {
        "chat_id": row["chat_id"], "character_id": None, "slot_id": row["slot_id"], "empty": True,
        "name": row["name"], "has_avatar": False, "position": row["position"], "depth": row["depth"],
        "importance": row["importance"], "is_me": False, "model_may_write": bool(row["model_may_write"]),
        "talkativeness": row["talkativeness"], "muted": bool(row["muted"]), "blurb": row["blurb"],
    }


class MemberRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    def list(self, chat_id: str, include_empty: bool = False) -> list[dict[str, Any]]:
        rows = self.db.fetchall(
            _SELECT + " WHERE cm.chat_id = ? ORDER BY cm.position, c.name COLLATE NOCASE", (chat_id,)
        )
        members = [_member(r) for r in rows]
        if include_empty:
            members += [_slot(r) for r in self.db.fetchall(_SLOT_SELECT + " WHERE chat_id = ?", (chat_id,))]
            members.sort(key=lambda m: (m["position"], m["name"].casefold()))
        return members

    def get(self, chat_id: str, character_id: str) -> dict[str, Any]:
        """A member by its key: the character id, or the slot id of an empty place."""
        row = self.db.fetchone(
            _SELECT + " WHERE cm.chat_id = ? AND cm.character_id = ?", (chat_id, character_id)
        )
        if row is not None:
            return _member(row)
        slot = self.db.fetchone(_SLOT_SELECT + " WHERE chat_id = ? AND slot_id = ?", (chat_id, character_id))
        if slot is None:
            raise NotFound(f"Character {character_id} is not in chat {chat_id}")
        return _slot(slot)

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
        if self.get(chat_id, character_id)["empty"]:
            return self._update_slot(chat_id, character_id, changes)
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
        if self.get(chat_id, character_id)["empty"]:
            self.db.execute("DELETE FROM chat_member_slots WHERE chat_id = ? AND slot_id = ?",
                            (chat_id, character_id))
            return
        self.db.execute(
            "DELETE FROM chat_members WHERE chat_id = ? AND character_id = ?", (chat_id, character_id)
        )

    # --- empty places ---------------------------------------------------------------------------

    def add_slot(self, chat_id: str, name: str, *, depth: int | None = None,
                 importance: int = DEFAULT_IMPORTANCE, model_may_write: bool = True,
                 talkativeness: int = DEFAULT_TALKATIVENESS, muted: bool = False,
                 blurb: str = "") -> dict[str, Any]:
        """An empty place that keeps `name` (no card is linked). A place with the same name is returned as is."""
        wanted = name.strip().casefold()
        for existing in self.list(chat_id, include_empty=True):
            if existing["empty"] and existing["name"].strip().casefold() == wanted:
                return existing
        slot_id = f"slot-{new_id()}"
        with self.db.transaction() as conn:
            position = conn.execute(
                "SELECT MAX(COALESCE((SELECT MAX(position) FROM chat_members WHERE chat_id = ?), -1),"
                " COALESCE((SELECT MAX(position) FROM chat_member_slots WHERE chat_id = ?), -1)) + 1",
                (chat_id, chat_id)).fetchone()[0]
            conn.execute(
                "INSERT INTO chat_member_slots (chat_id, slot_id, name, position, depth, importance, "
                "model_may_write, talkativeness, muted, blurb) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (chat_id, slot_id, name.strip(), position, depth, importance, int(model_may_write),
                 talkativeness, int(muted), blurb))
            conn.execute("UPDATE chats SET updated_at = ? WHERE id = ?", (now_iso(), chat_id))
        return self.get(chat_id, slot_id)

    def _update_slot(self, chat_id: str, slot_id: str, changes: dict[str, Any]) -> dict[str, Any]:
        with self.db.transaction() as conn:
            for key, value in changes.items():
                if key == "is_me":
                    if value:
                        raise ValueError('An empty place cannot be "me": replace it with a character first')
                    continue
                stored = int(value) if isinstance(value, bool) else value
                conn.execute(f"UPDATE chat_member_slots SET {key} = ? WHERE chat_id = ? AND slot_id = ?",
                             (stored, chat_id, slot_id))
        return self.get(chat_id, slot_id)

    def replace(self, chat_id: str, key: str, character_id: str) -> dict[str, Any]:
        """Put `character_id` in the place of the member `key` (a character or an empty place). The member's
        settings move with it, and the chat's messages the old member wrote now belong to the new character:
        by id for a character, by the author's name (no id) for an empty place."""
        old = self.get(chat_id, key)
        if old["character_id"] == character_id:
            return old
        if self.db.fetchone("SELECT 1 FROM chat_members WHERE chat_id = ? AND character_id = ?",
                            (chat_id, character_id)):
            raise Conflict("This character is already in the chat", code="already_member")
        with self.db.transaction() as conn:
            if old["empty"]:
                conn.execute("DELETE FROM chat_member_slots WHERE chat_id = ? AND slot_id = ?", (chat_id, key))
                conn.execute(
                    "INSERT INTO chat_members (chat_id, character_id, position, depth, importance, is_me, "
                    "model_may_write, talkativeness, muted, blurb) VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?, ?)",
                    (chat_id, character_id, old["position"], old["depth"], old["importance"],
                     int(old["model_may_write"]), old["talkativeness"], int(old["muted"]), old["blurb"]))
                wanted = old["name"].strip().casefold()
                rows = conn.execute("SELECT id, name FROM messages WHERE chat_id = ? AND role = 'assistant' "
                                    "AND character_id IS NULL", (chat_id,)).fetchall()
                for row in rows:
                    if (row["name"] or "").strip().casefold() == wanted:
                        conn.execute("UPDATE messages SET character_id = ? WHERE id = ?", (character_id, row["id"]))
            else:
                conn.execute("UPDATE chat_members SET character_id = ? WHERE chat_id = ? AND character_id = ?",
                             (character_id, chat_id, key))
                conn.execute("UPDATE messages SET character_id = ? WHERE chat_id = ? AND character_id = ?",
                             (character_id, chat_id, key))
            conn.execute("UPDATE chats SET updated_at = ? WHERE id = ?", (now_iso(), chat_id))
        return self.get(chat_id, character_id)
