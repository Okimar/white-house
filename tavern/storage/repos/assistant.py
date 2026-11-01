"""The assistant's conversations, their messages and the journal of its actions (stage 8б)."""

from __future__ import annotations

import json
from typing import Any

from tavern.errors import NotFound
from tavern.storage.db import Database
from tavern.util.ids import new_id, now_iso


def _message(row: Any) -> dict[str, Any]:
    out = dict(row)
    out["data"] = json.loads(out["data"])
    return out


def _action(row: Any, *, with_undo: bool = False) -> dict[str, Any]:
    out = dict(row)
    out["args"] = json.loads(out["args"])
    undo = json.loads(out.pop("undo"))
    if with_undo:
        out["undo"] = undo
    return out


class AssistantRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    # --- conversations ------------------------------------------------------------------------

    def chats(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self.db.fetchall("SELECT * FROM assistant_chats ORDER BY updated_at DESC, id")]

    def chat(self, chat_id: str) -> dict[str, Any]:
        row = self.db.fetchone("SELECT * FROM assistant_chats WHERE id = ?", (chat_id,))
        if row is None:
            raise NotFound("Беседа с помощником не найдена")
        return dict(row)

    def create_chat(self, title: str = "") -> dict[str, Any]:
        chat_id, now = new_id(), now_iso()
        self.db.execute("INSERT INTO assistant_chats (id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
                        (chat_id, title, now, now))
        return self.chat(chat_id)

    def rename_chat(self, chat_id: str, title: str) -> dict[str, Any]:
        self.chat(chat_id)
        self.db.execute("UPDATE assistant_chats SET title = ? WHERE id = ?", (title, chat_id))
        return self.chat(chat_id)

    def touch(self, chat_id: str) -> None:
        self.db.execute("UPDATE assistant_chats SET updated_at = ? WHERE id = ?", (now_iso(), chat_id))

    def delete_chat(self, chat_id: str) -> None:
        self.chat(chat_id)
        self.db.execute("DELETE FROM assistant_chats WHERE id = ?", (chat_id,))

    # --- messages -----------------------------------------------------------------------------

    def add_message(self, chat_id: str, role: str, text: str = "", data: dict[str, Any] | None = None) -> dict:
        cursor = self.db.execute(
            "INSERT INTO assistant_messages (chat_id, role, text, data, created_at) VALUES (?, ?, ?, ?, ?)",
            (chat_id, role, text, json.dumps(data or {}, ensure_ascii=False), now_iso()))
        self.touch(chat_id)
        return _message(self.db.fetchone("SELECT * FROM assistant_messages WHERE id = ?", (cursor.lastrowid,)))

    def messages(self, chat_id: str) -> list[dict[str, Any]]:
        rows = self.db.fetchall("SELECT * FROM assistant_messages WHERE chat_id = ? ORDER BY id", (chat_id,))
        return [_message(r) for r in rows]

    # --- the journal --------------------------------------------------------------------------

    def add_action(self, chat_id: str, tool: str, args: dict[str, Any], what: str, undo: dict[str, Any],
                   status: str = "done") -> dict[str, Any]:
        cursor = self.db.execute(
            "INSERT INTO assistant_actions (chat_id, tool, args, what, undo, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (chat_id, tool, json.dumps(args, ensure_ascii=False), what,
             json.dumps(undo, ensure_ascii=False), status, now_iso()))
        return self.action(int(cursor.lastrowid or 0))

    def action(self, action_id: int, *, with_undo: bool = False) -> dict[str, Any]:
        row = self.db.fetchone("SELECT * FROM assistant_actions WHERE id = ?", (action_id,))
        if row is None:
            raise NotFound("Действие не найдено")
        return _action(row, with_undo=with_undo)

    def actions(self, chat_id: str) -> list[dict[str, Any]]:
        rows = self.db.fetchall("SELECT * FROM assistant_actions WHERE chat_id = ? ORDER BY id", (chat_id,))
        return [_action(r) for r in rows]

    def done_since(self, chat_id: str, action_id: int) -> list[dict[str, Any]]:
        """Actions still in force from `action_id` on, newest first (with what undoes them)."""
        rows = self.db.fetchall(
            "SELECT * FROM assistant_actions WHERE chat_id = ? AND id >= ? AND status = 'done' ORDER BY id DESC",
            (chat_id, action_id))
        return [_action(r, with_undo=True) for r in rows]

    def set_status(self, action_id: int, status: str) -> None:
        self.db.execute("UPDATE assistant_actions SET status = ? WHERE id = ?", (status, action_id))
