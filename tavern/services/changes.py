"""«Изменения»: one list of everything the assistant changed, across all its conversations.

The journal (table assistant_actions, written by the assistant engine) is read here, never written.
A "commit" is what one request of the user made: the actions of one conversation after the same
request message. The list is paged by commits, newest first, and never loads the undo data of
the whole journal: details of one action (before / after) are read on demand.

Rolling back uses the assistant's own undo (services/assistant.undo), which puts back the chosen
action and EVERY later one of that conversation still in force. `plan` says in advance what that
takes along, and which later changes of other conversations touched the same rows.

The journal outlives its conversation (migration 0019): each action carries its request's id and text, and deleting a conversation keeps its actions, with
the conversation's name stored on them (`chat_title`), so they can still be rolled back. Actions are
grouped by `chat_ref`, the conversation's id that never goes away. Nothing prunes the journal.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from tavern.context import AppContext
from tavern.errors import Conflict
from tavern.storage.repos.assistant import conversation_title

log = logging.getLogger(__name__)

PAGE = 30
MAX_PAGE = 100
MAX_ACTIONS_SHOWN = 100  # per commit in the list
MAX_ENTRIES = 200        # rows in the details of one action
SCAN_LIMIT = 3000        # later actions of other conversations looked through for clashes
VALUE_LIMIT = 400

# The user's request a commit belongs to: kept on the action itself (req_id, req_text; the repo writes them
# when the action happens), so it stays after the conversation and its messages are deleted. 0 = none.
_REQUEST = "a.req_id"
# 1 when the action has anything to put back (rows, made rows, lorebook uses, themes).
_UNDOABLE = ("(COALESCE(json_array_length(a.undo, '$.rows'), 0) + COALESCE(json_array_length(a.undo, '$.created'), 0)"
             " + COALESCE(json_array_length(a.undo, '$.uses'), 0) + COALESCE(json_array_length(a.undo, '$.themes'), 0)) > 0")

KINDS = {
    "characters": "персонаж", "character_blocks": "пузырь", "block_links": "связь пузырей",
    "lorebooks": "лорбук", "lorebook_uses": "подключение лорбука", "chats": "чат", "messages": "сообщение",
    "swipes": "вариант ответа", "chat_members": "участник чата", "chat_member_slots": "пустое место участника", "presets": "пресет", "prompt_pieces": "кусок промпта",
    "connections": "подключение", "settings": "настройка", "author_notes": "заметка", "message_notes": "заметка к сообщению",
    "instruct_templates": "шаблон", "skills": "инструкция", "plugins": "дополнение", "plugin_data": "данные дополнения",
    "character_versions": "версия персонажа", "character_steps": "шаг истории персонажа",
}
FIELDS = {
    "name": "имя", "title": "заголовок", "content": "текст", "text": "текст", "description": "описание",
    "keywords": "ключевые слова", "mode": "режим", "depth": "глубина", "role": "роль", "chance": "шанс",
    "every": "каждые N ответов", "sticky": "липкость", "data": "данные", "value": "значение", "enabled": "включено",
    "scope": "где действует", "settings": "настройки", "rules": "правила", "avatar": "картинка", "key": "название",
    "spec": "формат", "muted": "заглушён", "blurb": "заметка о нём", "hidden": "скрыто", "layout": "вид",
    "model": "модель", "base_url": "адрес", "interval": "как часто",
}
SKIP_FIELDS = {"id", "created_at", "updated_at", "position", "extra", "leaf_id", "rowid"}
NAME_FIELDS = ("name", "title", "label")


def _titles(ctx: AppContext) -> tuple[dict[str, str], set[str]]:
    """Name of every conversation that has actions (kept name for deleted ones) and the ids still alive."""
    live = {r["id"]: conversation_title(r["title"], r["created_at"])
            for r in ctx.db.fetchall("SELECT id, title, created_at FROM assistant_chats")}
    titles = dict(live)
    for r in ctx.db.fetchall("SELECT chat_ref, chat_title FROM assistant_actions GROUP BY chat_ref"):
        titles.setdefault(r["chat_ref"], r["chat_title"] or "Беседа")
    return titles, set(live)


def _short(text: Any, limit: int = 300) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


# --- the list ---------------------------------------------------------------------------------


def _status(actions: list[dict[str, Any]]) -> str:
    states = {a["status"] for a in actions}
    return "undone" if states == {"undone"} else "done" if states == {"done"} else "partly"


def list_changes(ctx: AppContext, *, since: str = "", until: str = "", conversation: str = "",
                 before: int = 0, limit: int = PAGE) -> dict[str, Any]:
    """One page of commits, newest first. `since`/`until` are UTC timestamps (an action's time),
    `before` is the cursor from the previous page (`next`)."""
    limit = max(1, min(int(limit or PAGE), MAX_PAGE))
    where, params = ["a.status IN ('done', 'undone')"], []
    if since:
        where.append("a.created_at >= ?")
        params.append(since)
    if until:
        where.append("a.created_at < ?")
        params.append(until)
    if conversation:
        where.append("a.chat_ref = ?")
        params.append(conversation)
    rows = ctx.db.fetchall(
        f"SELECT chat_id, req, MIN(id) AS first_id, MAX(id) AS last_id FROM ("
        f"SELECT a.id, a.chat_ref AS chat_id, {_REQUEST} AS req FROM assistant_actions a WHERE {' AND '.join(where)}) "
        f"GROUP BY chat_id, req HAVING MAX(id) < ? ORDER BY last_id DESC LIMIT ?",
        (*params, before or 1 << 62, limit + 1))
    more = len(rows) > limit
    rows = rows[:limit]
    titles, live = _titles(ctx)
    commits = [_commit(ctx, r["chat_id"], r["req"], r["first_id"], r["last_id"], titles, live) for r in rows]
    out: dict[str, Any] = {"commits": commits, "next": rows[-1]["last_id"] if more and rows else None}
    if not before:
        out["conversations"] = [
            {"id": r["id"], "title": titles.get(r["id"], ""), "count": r["n"], "deleted": r["id"] not in live}
            for r in ctx.db.fetchall(
                "SELECT chat_ref AS id, COUNT(*) AS n, MAX(id) AS last FROM assistant_actions "
                "WHERE status IN ('done', 'undone') GROUP BY chat_ref ORDER BY last DESC")]
        out["total"] = ctx.db.fetchone("SELECT COUNT(*) AS n FROM assistant_actions "
                                       "WHERE status IN ('done', 'undone')")["n"]
    return out


def _commit(ctx: AppContext, chat_id: str, req: int | None, first_id: int, last_id: int,
            titles: dict[str, str], live: set[str]) -> dict[str, Any]:
    acts = [dict(r) for r in ctx.db.fetchall(
        f"SELECT a.id, a.tool, a.what, a.status, a.created_at, {_UNDOABLE} AS undoable FROM assistant_actions a "
        f"WHERE a.chat_ref = ? AND a.id BETWEEN ? AND ? AND a.status IN ('done', 'undone') "
        f"AND {_REQUEST} IS ? ORDER BY a.id", (chat_id, first_id, last_id, req))]
    for a in acts:
        a["undoable"] = bool(a["undoable"])
    request = ""
    if req:
        row = ctx.db.fetchone("SELECT req_text FROM assistant_actions WHERE chat_ref = ? AND req_id = ? LIMIT 1",
                              (chat_id, req))
        request = _short(row["req_text"]) if row else ""
    done = [a for a in acts if a["status"] == "done"]
    status = _status(acts)
    return {
        "key": f"{chat_id}:{req or 0}", "chat_id": chat_id, "conversation": titles.get(chat_id, ""),
        "conversation_deleted": chat_id not in live,
        "request": request, "at": acts[0]["created_at"], "until": acts[-1]["created_at"], "count": len(acts),
        "status": status, "rollback_id": done[0]["id"] if done else None,
        "can_rollback": bool(done) and any(a["undoable"] for a in done),
        "actions": [{k: a[k] for k in ("id", "what", "status", "created_at", "undoable")}
                    for a in acts[:MAX_ACTIONS_SHOWN]],
        "more_actions": max(0, len(acts) - MAX_ACTIONS_SHOWN),
    }


# --- what exactly changed (on demand) ---------------------------------------------------------


def _plain(value: Any) -> Any:
    if isinstance(value, bytes):
        return f"[данные, {len(value)} байт]"
    if isinstance(value, dict) and "$b" in value:
        return f"[данные, {len(value['$b']) * 3 // 4} байт]"
    return value


def _table_columns(ctx: AppContext) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for r in ctx.db.fetchall("SELECT name FROM sqlite_master WHERE type = 'table'"):
        out[r["name"]] = {c["name"] for c in ctx.db.connection.execute(f'PRAGMA table_info("{r["name"]}")')}
    return out


def _current(ctx: AppContext, tables: dict[str, set[str]], table: str, key: dict[str, Any]) -> dict[str, Any] | None:
    columns = tables.get(table)
    if columns is None or not key:
        return None
    if any(k not in columns and k != "rowid" for k in key):
        return None
    cond = " AND ".join(f'"{k}" IS ?' for k in key)
    row = ctx.db.connection.execute(f'SELECT * FROM "{table}" WHERE {cond}', tuple(key.values())).fetchone()
    return dict(row) if row is not None else None


def _name(row: dict[str, Any] | None) -> str:
    for field in NAME_FIELDS:
        if row and isinstance(row.get(field), str) and row[field].strip():
            return _short(row[field], 120)
    return ""


def _view(row: dict[str, Any] | None, only: set[str] | None = None) -> dict[str, str] | None:
    if row is None:
        return None
    out = {}
    for k, v in row.items():
        if k in SKIP_FIELDS or (only is not None and k not in only):
            continue
        out[FIELDS.get(k, k)] = _short(_plain(v), VALUE_LIMIT)
    return out


def details(ctx: AppContext, action_id: int) -> dict[str, Any]:
    """What one action changed, in plain words: kind, name, created / changed / deleted, before and after."""
    action = ctx.assistant_repo.action(action_id, with_undo=True)
    undo = action["undo"] or {}
    tables = _table_columns(ctx)
    entries: list[dict[str, Any]] = []
    total = 0
    for item in undo.get("created", []):
        total += 1
        if len(entries) < MAX_ENTRIES:
            now = _current(ctx, tables, item["table"], item["key"])
            entries.append({"kind": KINDS.get(item["table"], "запись"), "name": _name(now), "state": "created",
                            "before": None, "after": _view(now), "gone": now is None})
    for item in undo.get("rows", []):
        if item.get("link"):
            continue
        total += 1
        if len(entries) >= MAX_ENTRIES:
            continue
        before = {k: _plain(v) for k, v in item["row"].items()}
        now = _current(ctx, tables, item["table"], item["key"])
        if now is None:
            state, after, shown = "deleted", None, set(before)
        else:
            now_plain = {k: _plain(v) for k, v in now.items()}
            differ = {k for k in before if k not in SKIP_FIELDS and before[k] != now_plain.get(k)}
            if not differ:
                total -= 1
                continue
            state, after, shown = "changed", _view(now, differ), differ
        entries.append({"kind": KINDS.get(item["table"], "запись"), "name": _name(before) or _name(now),
                        "state": state, "before": _view(before, shown), "after": after, "gone": False})
    if undo.get("uses"):
        entries.append({"kind": "подключение лорбуков", "name": "", "state": "changed", "before": None,
                        "after": None, "gone": False})
    if undo.get("themes"):
        entries.append({"kind": "тема оформления", "name": "", "state": "changed", "before": None,
                        "after": None, "gone": False})
    return {"id": action["id"], "what": action["what"], "status": action["status"], "entries": entries,
            "more": max(0, total - len(entries))}


# --- rolling back -----------------------------------------------------------------------------


def _touched(undo: dict[str, Any]) -> set[tuple[str, str]]:
    keys: set[tuple[str, str]] = set()
    for item in [*undo.get("rows", []), *undo.get("created", [])]:
        if item.get("link"):
            continue
        keys.add((item["table"], json.dumps(item["key"], sort_keys=True)))
    return keys


def plan(ctx: AppContext, action_id: int) -> dict[str, Any]:
    """What «Откатить» of the commit starting with this action would do, before doing it."""
    from tavern.services import assistant, assistant_app  # the services import each other lazily

    repo = ctx.assistant_repo
    action = repo.action(action_id)
    titles, _live = _titles(ctx)
    out: dict[str, Any] = {"action_id": action_id, "conversation": titles.get(action["chat_id"], ""), "can": True,
                           "reason": "", "this": [], "later": [], "clashes": [], "scanned_all": True,
                           "needs_confirm": False}
    if action["status"] != "done":
        return out | {"can": False, "reason": "Это изменение уже откатено или не действует"}
    if ctx.runs.active(assistant.run_key(action["chat_id"])) is not None:
        return out | {"can": False, "reason": "Помощник сейчас работает в этой беседе: остановите его или дождитесь конца"}
    if assistant_app.busy_with_replies(ctx):
        return out | {"can": False, "reason": "Сейчас пишется ответ в чате: откатить можно, когда он закончится"}
    items = repo.done_since(action["chat_id"], action_id)  # newest first
    this_request = action["req_id"]
    later: dict[int, dict[str, Any]] = {}
    for item in reversed(items):
        req = item["req_id"]
        if req == this_request:
            out["this"].append(item["what"])
        else:
            group = later.setdefault(req, {"request": "", "at": item["created_at"], "whats": []})
            if not group["request"] and req:
                group["request"] = _short(item["req_text"], 120)
            group["whats"].append(item["what"])
    out["later"] = list(later.values())
    mine = set()
    for item in items:
        mine |= _touched(item["undo"])
    if mine:
        scanned = 0
        rows = ctx.db.fetchall(
            "SELECT id, chat_ref AS chat_id, what, created_at, undo FROM assistant_actions WHERE status = 'done' AND chat_ref != ? "
            "AND id > ? ORDER BY id LIMIT ?", (action["chat_id"], action_id, SCAN_LIMIT + 1))
        for row in rows[:SCAN_LIMIT]:
            scanned += 1
            try:
                undo = json.loads(row["undo"])
            except ValueError:
                continue
            if mine & _touched(undo) and len(out["clashes"]) < 20:
                out["clashes"].append({"id": row["id"], "conversation": titles.get(row["chat_id"], ""),
                                       "what": row["what"], "at": row["created_at"]})
        out["scanned_all"] = len(rows) <= SCAN_LIMIT
    out["needs_confirm"] = bool(out["later"] or out["clashes"])
    return out


async def rollback(ctx: AppContext, action_id: int, accepted: bool = False) -> dict[str, Any]:
    """Roll back with the assistant's own undo. Anything that would also take along later work, or
    clash with another conversation's changes, is done only when the person has seen it (`accepted`)."""
    from tavern.services import assistant

    p = plan(ctx, action_id)
    if not p["can"]:
        raise Conflict(p["reason"], code="cannot_rollback")
    if p["needs_confirm"] and not accepted:
        raise Conflict("Откат затронет и другие изменения: сначала прочтите, что именно, и подтвердите",
                       code="needs_confirm", details=p)
    return {"undone": await assistant.undo(ctx, action_id)}
