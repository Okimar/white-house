"""Chats as a tree of messages with swipes.

A message is a node (`parent_id`, `depth`); its text lives in `swipes` rows and
`messages.active_swipe` picks the visible one. `chats.leaf_id` is the tip of the active branch.
The active path is the chain root -> leaf, so long chats are paged by `depth`.

Message dict shape returned to callers:
    id, chat_id, parent_id, depth, role, name, hidden, exclude_from_prompt, owner,
    content (active swipe text), swipe_meta (the active swipe's: reasoning, images, ...),
    active_swipe, swipe_count, extra, created_at
"""

from __future__ import annotations

import json
from typing import Any

from tavern.errors import Conflict, NotFound
from tavern.storage.db import Database
from tavern.util.ids import new_id, now_iso

ROLES = ("user", "assistant", "system")

_MESSAGE_SELECT = """
SELECT m.id, m.chat_id, m.parent_id, m.depth, m.role, m.name, m.hidden, m.exclude_from_prompt,
       m.owner, m.active_swipe, m.extra, m.created_at,
       COALESCE(s.content, '') AS content, COALESCE(s.meta, '{}') AS swipe_meta,
       (SELECT COUNT(*) FROM swipes WHERE message_id = m.id) AS swipe_count
FROM messages m
LEFT JOIN swipes s ON s.message_id = m.id AND s.idx = m.active_swipe
"""


def _message(row: Any) -> dict[str, Any]:
    return {
        "id": row["id"],
        "chat_id": row["chat_id"],
        "parent_id": row["parent_id"],
        "depth": row["depth"],
        "role": row["role"],
        "name": row["name"],
        "hidden": bool(row["hidden"]),
        "exclude_from_prompt": bool(row["exclude_from_prompt"]),
        "owner": row["owner"],
        "content": row["content"],
        "swipe_meta": json.loads(row["swipe_meta"]),
        "active_swipe": row["active_swipe"],
        "swipe_count": row["swipe_count"],
        "extra": json.loads(row["extra"]),
        "created_at": row["created_at"],
    }


def _chat(row: Any) -> dict[str, Any]:
    return {
        "id": row["id"],
        "character_id": row["character_id"],
        "title": row["title"],
        "leaf_id": row["leaf_id"],
        "meta": json.loads(row["meta"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


class ChatRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    # --- chats -------------------------------------------------------------------------------

    def create_chat(
        self, *, character_id: str | None, title: str, meta: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        chat_id, now = new_id(), now_iso()
        self.db.execute(
            "INSERT INTO chats (id, character_id, title, meta, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (chat_id, character_id, title, json.dumps(meta or {}), now, now),
        )
        return self.get_chat(chat_id)

    def get_chat(self, chat_id: str) -> dict[str, Any]:
        row = self.db.fetchone("SELECT * FROM chats WHERE id = ?", (chat_id,))
        if row is None:
            raise NotFound(f"Chat {chat_id} not found")
        return _chat(row)

    def list_chats(self, character_id: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        """Newest first, with message counts of the active branch omitted (cheap listing)."""
        if character_id is None:
            rows = self.db.fetchall("SELECT * FROM chats ORDER BY updated_at DESC LIMIT ?", (limit,))
        else:
            rows = self.db.fetchall(
                "SELECT * FROM chats WHERE character_id = ? ORDER BY updated_at DESC LIMIT ?",
                (character_id, limit),
            )
        return [_chat(r) for r in rows]

    def rename_chat(self, chat_id: str, title: str) -> dict[str, Any]:
        self.get_chat(chat_id)
        self.db.execute("UPDATE chats SET title = ? WHERE id = ?", (title, chat_id))
        return self.get_chat(chat_id)

    def set_character(self, chat_id: str, character_id: str | None) -> None:
        """Change the chat's main character (the one replies are named after)."""
        self.db.execute("UPDATE chats SET character_id = ? WHERE id = ?", (character_id, chat_id))

    def update_meta(self, chat_id: str, meta: dict[str, Any]) -> None:
        self.get_chat(chat_id)
        self.db.execute("UPDATE chats SET meta = ? WHERE id = ?", (json.dumps(meta), chat_id))

    def delete_chat(self, chat_id: str) -> None:
        self.get_chat(chat_id)
        self.db.execute("DELETE FROM chats WHERE id = ?", (chat_id,))

    def _touch(self, chat_id: str) -> None:
        self.db.execute("UPDATE chats SET updated_at = ? WHERE id = ?", (now_iso(), chat_id))

    # --- messages ----------------------------------------------------------------------------

    def all_messages(self, chat_id: str) -> list[dict[str, Any]]:
        """Return every message node in tree order for complete chat export."""
        self.get_chat(chat_id)
        rows = self.db.fetchall(
            _MESSAGE_SELECT + " WHERE m.chat_id = ? ORDER BY m.depth, m.created_at, m.id",
            (chat_id,),
        )
        return [_message(row) for row in rows]

    def search_messages(self, chat_id: str, query: str, *, limit: int = 100) -> list[dict[str, Any]]:
        """Case-insensitive literal search across stored message text."""
        self.get_chat(chat_id)
        escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        rows = self.db.fetchall(
            _MESSAGE_SELECT + " WHERE m.chat_id = ? AND lower(content) LIKE lower(?) ESCAPE '\\' "
            "ORDER BY m.depth, m.created_at LIMIT ?",
            (chat_id, f"%{escaped}%", limit),
        )
        return [_message(row) for row in rows]

    def get_message(self, message_id: str) -> dict[str, Any]:
        row = self.db.fetchone(_MESSAGE_SELECT + " WHERE m.id = ?", (message_id,))
        if row is None:
            raise NotFound(f"Message {message_id} not found")
        return _message(row)

    def child_messages(self, message_id: str) -> list[dict[str, Any]]:
        """Return direct branch alternatives below one message."""
        self.get_message(message_id)
        rows = self.db.fetchall(
            _MESSAGE_SELECT + " WHERE m.parent_id = ? ORDER BY m.created_at, m.id",
            (message_id,),
        )
        return [_message(row) for row in rows]

    def add_message(
        self,
        chat_id: str,
        *,
        role: str,
        content: str,
        parent_id: str | None = None,
        name: str = "",
        hidden: bool = False,
        exclude_from_prompt: bool = False,
        owner: str = "",
        extra: dict[str, Any] | None = None,
        set_leaf: bool = True,
    ) -> dict[str, Any]:
        """Append a node under `parent_id` (None = root). Moves the chat's leaf to it by default."""
        if role not in ROLES:
            raise ValueError(f"Unknown role {role!r}")
        message_id, now = new_id(), now_iso()
        with self.db.transaction() as conn:
            if conn.execute("SELECT 1 FROM chats WHERE id = ?", (chat_id,)).fetchone() is None:
                raise NotFound(f"Chat {chat_id} not found")
            depth = 0
            if parent_id is not None:
                parent = conn.execute(
                    "SELECT depth, chat_id FROM messages WHERE id = ?", (parent_id,)
                ).fetchone()
                if parent is None or parent["chat_id"] != chat_id:
                    raise NotFound(f"Parent message {parent_id} not found in chat {chat_id}")
                depth = parent["depth"] + 1
            conn.execute(
                "INSERT INTO messages (id, chat_id, parent_id, depth, role, name, hidden, "
                "exclude_from_prompt, owner, extra, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    message_id, chat_id, parent_id, depth, role, name, int(hidden),
                    int(exclude_from_prompt), owner, json.dumps(extra or {}), now,
                ),
            )
            conn.execute(
                "INSERT INTO swipes (message_id, idx, content, created_at) VALUES (?, 0, ?, ?)",
                (message_id, content, now),
            )
            if set_leaf:
                conn.execute("UPDATE chats SET leaf_id = ?, updated_at = ? WHERE id = ?",
                             (message_id, now, chat_id))
        return self.get_message(message_id)

    def set_content(self, message_id: str, content: str, *, swipe_idx: int | None = None) -> None:
        """Replace text of a swipe (default: the active one)."""
        message = self.get_message(message_id)
        idx = message["active_swipe"] if swipe_idx is None else swipe_idx
        cursor = self.db.execute(
            "UPDATE swipes SET content = ? WHERE message_id = ? AND idx = ?",
            (content, message_id, idx),
        )
        if cursor.rowcount == 0:
            raise NotFound(f"Swipe {idx} of message {message_id} not found")
        self._touch(message["chat_id"])

    def update_swipe_meta(self, message_id: str, swipe_idx: int, patch: dict[str, Any]) -> None:
        """Merge keys into one swipe's meta; a None value removes the key."""
        with self.db.transaction() as conn:
            row = conn.execute(
                "SELECT meta FROM swipes WHERE message_id = ? AND idx = ?", (message_id, swipe_idx)
            ).fetchone()
            if row is None:
                raise NotFound(f"Swipe {swipe_idx} of message {message_id} not found")
            meta = {**json.loads(row["meta"]), **patch}
            meta = {k: v for k, v in meta.items() if v is not None}
            conn.execute("UPDATE swipes SET meta = ? WHERE message_id = ? AND idx = ?",
                         (json.dumps(meta), message_id, swipe_idx))

    def set_roles(self, chat_id: str, changes: dict[str, tuple[str, str]]) -> list[dict[str, Any]]:
        """Set (role, name) of several messages of one chat at once: all or nothing."""
        for role, _ in changes.values():
            if role not in ROLES:
                raise ValueError(f"Unknown role {role!r}")
        with self.db.transaction() as conn:
            for message_id, (role, name) in changes.items():
                row = conn.execute("SELECT chat_id FROM messages WHERE id = ?", (message_id,)).fetchone()
                if row is None or row["chat_id"] != chat_id:
                    raise NotFound(f"Message {message_id} not found in chat {chat_id}")
                conn.execute(
                    "UPDATE messages SET role = ?, name = ? WHERE id = ?", (role, name, message_id)
                )
            conn.execute("UPDATE chats SET updated_at = ? WHERE id = ?", (now_iso(), chat_id))
        return [self.get_message(message_id) for message_id in changes]

    def get_swipes(self, message_id: str) -> list[str]:
        """Return all swipe texts in index order for export."""
        self.get_message(message_id)
        rows = self.db.fetchall(
            "SELECT content FROM swipes WHERE message_id = ? ORDER BY idx", (message_id,)
        )
        return [row["content"] for row in rows]

    def list_swipes(self, message_id: str) -> list[dict[str, Any]]:
        """Every version of a message: index, text and meta (who wrote it, thinking, pictures)."""
        self.get_message(message_id)
        rows = self.db.fetchall(
            "SELECT idx, content, meta FROM swipes WHERE message_id = ? ORDER BY idx", (message_id,)
        )
        return [{"index": r["idx"], "content": r["content"], "meta": json.loads(r["meta"])} for r in rows]

    def add_swipe(self, message_id: str, content: str = "", meta: dict[str, Any] | None = None) -> int:
        """Add an alternative text and make it the active one. Returns its index."""
        with self.db.transaction() as conn:
            row = conn.execute("SELECT 1 FROM messages WHERE id = ?", (message_id,)).fetchone()
            if row is None:
                raise NotFound(f"Message {message_id} not found")
            idx = conn.execute(
                "SELECT COALESCE(MAX(idx), -1) + 1 FROM swipes WHERE message_id = ?", (message_id,)
            ).fetchone()[0]
            conn.execute(
                "INSERT INTO swipes (message_id, idx, content, meta, created_at) VALUES (?,?,?,?,?)",
                (message_id, idx, content, json.dumps(meta or {}), now_iso()),
            )
            conn.execute("UPDATE messages SET active_swipe = ? WHERE id = ?", (idx, message_id))
        return idx

    def remove_last_swipe(self, message_id: str) -> None:
        """Drop the newest swipe (used to discard an empty failed regeneration)."""
        with self.db.transaction() as conn:
            last = conn.execute(
                "SELECT MAX(idx) FROM swipes WHERE message_id = ?", (message_id,)
            ).fetchone()[0]
            if last is None or last == 0:
                raise Conflict("A message must keep at least one swipe")
            conn.execute("DELETE FROM swipes WHERE message_id = ? AND idx = ?", (message_id, last))
            conn.execute(
                "UPDATE messages SET active_swipe = ? WHERE id = ? AND active_swipe >= ?",
                (last - 1, message_id, last),
            )

    def set_active_swipe(self, message_id: str, idx: int) -> dict[str, Any]:
        message = self.get_message(message_id)
        if not 0 <= idx < message["swipe_count"]:
            raise Conflict(f"Swipe {idx} out of range (0..{message['swipe_count'] - 1})")
        self.db.execute("UPDATE messages SET active_swipe = ? WHERE id = ?", (idx, message_id))
        self._touch(message["chat_id"])
        return self.get_message(message_id)

    def set_leaf(self, chat_id: str, message_id: str | None) -> None:
        if message_id is not None and self.get_message(message_id)["chat_id"] != chat_id:
            raise NotFound(f"Message {message_id} is not in chat {chat_id}")
        self.db.execute(
            "UPDATE chats SET leaf_id = ?, updated_at = ? WHERE id = ?",
            (message_id, now_iso(), chat_id),
        )

    def newest_leaf(self, message_id: str) -> str:
        """Follow the newest child down from a message to the end of its branch."""
        current = self.get_message(message_id)["id"]
        while True:
            row = self.db.fetchone(
                "SELECT id FROM messages WHERE parent_id = ? ORDER BY created_at DESC, id DESC LIMIT 1",
                (current,),
            )
            if row is None:
                return current
            current = row["id"]

    # --- reading the active branch -----------------------------------------------------------

    def active_path(
        self,
        chat_id: str,
        *,
        before_depth: int | None = None,
        limit: int | None = None,
        include_hidden: bool = True,
    ) -> list[dict[str, Any]]:
        """Messages of the active branch in chat order (oldest first).

        `before_depth`/`limit` page backwards: the newest `limit` messages with depth
        < before_depth. Hidden messages are skipped before counting when include_hidden=False.
        """
        chat = self.get_chat(chat_id)
        if chat["leaf_id"] is None:
            return []
        conditions = ["1=1"]
        params: list[Any] = [chat["leaf_id"]]
        if before_depth is not None:
            conditions.append("m.depth < ?")
            params.append(before_depth)
        if not include_hidden:
            conditions.append("m.hidden = 0")
        limit_sql = ""
        if limit is not None:
            limit_sql = " LIMIT ?"
            params.append(limit)
        sql = (
            "WITH RECURSIVE path(id, parent_id) AS ("
            "  SELECT id, parent_id FROM messages WHERE id = ?"
            "  UNION ALL"
            "  SELECT p.id, p.parent_id FROM messages p JOIN path ON p.id = path.parent_id"
            ") "
            + _MESSAGE_SELECT
            + " JOIN path ON path.id = m.id WHERE "
            + " AND ".join(conditions)
            + " ORDER BY m.depth DESC"
            + limit_sql
        )
        rows = self.db.fetchall(sql, params)
        return [_message(r) for r in reversed(rows)]

    def path_length(self, chat_id: str) -> int:
        """Number of messages on the active branch (leaf depth + 1)."""
        chat = self.get_chat(chat_id)
        if chat["leaf_id"] is None:
            return 0
        return self.get_message(chat["leaf_id"])["depth"] + 1

    def delete_message(self, message_id: str) -> None:
        """Delete a message and everything below it. Moves the leaf to the parent if needed."""
        message = self.get_message(message_id)
        chat = self.get_chat(message["chat_id"])
        with self.db.transaction() as conn:
            leaf_below = conn.execute(
                "WITH RECURSIVE sub(id) AS (SELECT ? UNION ALL "
                "SELECT m.id FROM messages m JOIN sub ON m.parent_id = sub.id) "
                "SELECT 1 FROM sub WHERE id = ?",
                (message_id, chat["leaf_id"]),
            ).fetchone()
            conn.execute("DELETE FROM messages WHERE id = ?", (message_id,))
            if leaf_below is not None:
                conn.execute(
                    "UPDATE chats SET leaf_id = ?, updated_at = ? WHERE id = ?",
                    (message["parent_id"], now_iso(), chat["id"]),
                )
