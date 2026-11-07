"""The assistant's conversations, their messages, the journal of its actions (stage 8б), the task a
conversation works on and «Что я о вас знаю» (stage 10а)."""

from __future__ import annotations

import json
from typing import Any

from tavern.errors import NotFound
from tavern.storage.db import Database
from tavern.util.ids import new_id, now_iso


def conversation_title(title: str, created_at: str) -> str:
    return title.strip() or f"Беседа от {created_at[:10]}"


def _message(row: Any) -> dict[str, Any]:
    out = dict(row)
    out["data"] = json.loads(out["data"])
    return out


def _chat(row: Any) -> dict[str, Any]:
    out = dict(row)
    out["task"] = json.loads(out.get("task") or "{}")
    return out


def _action(row: Any, *, with_undo: bool = False) -> dict[str, Any]:
    out = dict(row)
    out["args"] = json.loads(out["args"])
    out["chat_id"] = out.pop("chat_ref")  # the conversation's id, also after it was deleted (chat_id column is null then)
    undo = json.loads(out.pop("undo"))
    if with_undo:
        out["undo"] = undo
    return out


class AssistantRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    # --- conversations ------------------------------------------------------------------------

    def chats(self) -> list[dict[str, Any]]:
        return [_chat(r) for r in self.db.fetchall("SELECT * FROM assistant_chats ORDER BY updated_at DESC, id")]

    def chat(self, chat_id: str) -> dict[str, Any]:
        row = self.db.fetchone("SELECT * FROM assistant_chats WHERE id = ?", (chat_id,))
        if row is None:
            raise NotFound("Беседа с помощником не найдена")
        return _chat(row)

    def create_chat(self, title: str = "", created_at: str | None = None, updated_at: str | None = None) -> dict[str, Any]:
        chat_id, now = new_id(), now_iso()
        self.db.execute("INSERT INTO assistant_chats (id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
                        (chat_id, title, created_at or now, updated_at or now))
        return self.chat(chat_id)

    def rename_chat(self, chat_id: str, title: str) -> dict[str, Any]:
        self.chat(chat_id)
        self.db.execute("UPDATE assistant_chats SET title = ? WHERE id = ?", (title, chat_id))
        return self.chat(chat_id)

    def touch(self, chat_id: str) -> None:
        self.db.execute("UPDATE assistant_chats SET updated_at = ? WHERE id = ?", (now_iso(), chat_id))

    def task(self, chat_id: str) -> dict[str, Any]:
        return self.chat(chat_id)["task"]

    def set_task(self, chat_id: str, task: dict[str, Any]) -> None:
        self.db.execute("UPDATE assistant_chats SET task = ? WHERE id = ?",
                        (json.dumps(task, ensure_ascii=False), chat_id))

    def delete_chat(self, chat_id: str) -> None:
        chat = self.chat(chat_id)
        # The journal stays (to roll back with): it keeps the conversation's name from here on.
        self.db.execute("UPDATE assistant_actions SET chat_title = ? WHERE chat_ref = ?",
                        (conversation_title(chat["title"], chat["created_at"]), chat_id))
        self.db.execute("DELETE FROM assistant_chats WHERE id = ?", (chat_id,))

    # --- messages -----------------------------------------------------------------------------

    def add_message(self, chat_id: str, role: str, text: str = "", data: dict[str, Any] | None = None,
                    created_at: str | None = None) -> dict:
        """`created_at` is for an imported conversation: it keeps its own times and its place in the list."""
        cursor = self.db.execute(
            "INSERT INTO assistant_messages (chat_id, role, text, data, created_at) VALUES (?, ?, ?, ?, ?)",
            (chat_id, role, text, json.dumps(data or {}, ensure_ascii=False), created_at or now_iso()))
        if created_at is None:
            self.touch(chat_id)
        return _message(self.db.fetchone("SELECT * FROM assistant_messages WHERE id = ?", (cursor.lastrowid,)))

    def messages(self, chat_id: str) -> list[dict[str, Any]]:
        rows = self.db.fetchall("SELECT * FROM assistant_messages WHERE chat_id = ? ORDER BY id", (chat_id,))
        return [_message(r) for r in rows]

    # --- the journal --------------------------------------------------------------------------

    def add_action(self, chat_id: str, tool: str, args: dict[str, Any], what: str, undo: dict[str, Any],
                   status: str = "done", created_at: str | None = None) -> dict[str, Any]:
        when = created_at or now_iso()
        # The request is written onto the action now: the messages go away with the conversation, the journal stays.
        req = self.db.fetchone(
            "SELECT id, text FROM assistant_messages WHERE chat_id = ? AND role = 'user' AND data = '{}' "
            "AND text NOT IN ('Давай', 'Продолжай') AND created_at <= ? ORDER BY id DESC LIMIT 1", (chat_id, when))
        cursor = self.db.execute(
            "INSERT INTO assistant_actions (chat_id, chat_ref, req_id, req_text, tool, args, what, undo, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (chat_id, chat_id, req["id"] if req else 0, req["text"] if req else "", tool,
             json.dumps(args, ensure_ascii=False), what, json.dumps(undo, ensure_ascii=False), status, when))
        return self.action(int(cursor.lastrowid or 0))

    def action(self, action_id: int, *, with_undo: bool = False) -> dict[str, Any]:
        row = self.db.fetchone("SELECT * FROM assistant_actions WHERE id = ?", (action_id,))
        if row is None:
            raise NotFound("Действие не найдено")
        return _action(row, with_undo=with_undo)

    def actions(self, chat_id: str) -> list[dict[str, Any]]:
        rows = self.db.fetchall("SELECT * FROM assistant_actions WHERE chat_ref = ? ORDER BY id", (chat_id,))
        return [_action(r) for r in rows]

    def done_since(self, chat_id: str, action_id: int) -> list[dict[str, Any]]:
        """Actions still in force from `action_id` on, newest first (with what undoes them)."""
        rows = self.db.fetchall(
            "SELECT * FROM assistant_actions WHERE chat_ref = ? AND id >= ? AND status = 'done' ORDER BY id DESC",
            (chat_id, action_id))
        return [_action(r, with_undo=True) for r in rows]

    def set_status(self, action_id: int, status: str) -> None:
        self.db.execute("UPDATE assistant_actions SET status = ? WHERE id = ?", (status, action_id))

    # --- «Что я о вас знаю» ---------------------------------------------------------------------

    def memory(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self.db.fetchall("SELECT * FROM assistant_memory ORDER BY id")]

    def remember(self, text: str, chat_id: str | None = None) -> dict[str, Any]:
        cursor = self.db.execute("INSERT INTO assistant_memory (text, chat_id, created_at) VALUES (?, ?, ?)",
                                 (text, chat_id, now_iso()))
        return self.fact(int(cursor.lastrowid or 0))

    def fact(self, fact_id: int) -> dict[str, Any]:
        row = self.db.fetchone("SELECT * FROM assistant_memory WHERE id = ?", (fact_id,))
        if row is None:
            raise NotFound("Такой записи в памяти помощника нет")
        return dict(row)

    def edit_fact(self, fact_id: int, text: str) -> dict[str, Any]:
        self.fact(fact_id)
        self.db.execute("UPDATE assistant_memory SET text = ? WHERE id = ?", (text, fact_id))
        return self.fact(fact_id)

    def forget(self, fact_id: int) -> None:
        self.fact(fact_id)
        self.db.execute("DELETE FROM assistant_memory WHERE id = ?", (fact_id,))
