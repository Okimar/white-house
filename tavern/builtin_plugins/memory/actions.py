"""The memory's M3 actions for the «Память» window: summaries (hand edit with a lock, lock, versions, «Пересказать
заново»), «Сюжет до сих пор» (hand edit, lock, versions, «Пересобрать») and the members' flags («Помнит всё»,
«Не наслаивать»). ADR 0019 sections 3.2, 6.1.

Hand edits that change a text go into the same undo log as the chapter edits (`chapters.log_text`), so the window's
one «↶» undoes them in order. A hand-written summary or plot is LOCKED: automatic work never replaces it until the
lock is taken off. Each function of the «hand» kind gets (chat id, branch path, payload, plugin storage, persona id)
from server.py, which checks that memory is on in the chat and gives the new view back.
"""

from __future__ import annotations

from typing import Any

from tavern.errors import AppError
from tavern.storage.db import Database

from . import chapters, store

MAX_TEXT = 20000


def _need(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise AppError(f"Нужен {key}", code="bad_request")
    return value


def _body(payload: dict[str, Any], what: str) -> str:
    text = payload.get("text")
    if not isinstance(text, str) or not text.strip():
        raise AppError(f"Пустой {what}", code="bad_request")
    return text.strip()[:MAX_TEXT]


def on_path(db: Database, chat_id: str, path: list[dict[str, Any]], chapter_id: str) -> chapters.Placed:
    found = next((x for x in chapters.placed(store.chapter_rows(db, chat_id), path) if x.row["id"] == chapter_id),
                 None)
    if found is None:
        raise chapters.ChapterError("Эта глава не относится к открытой ветке чата")
    return found


def summary_edit(db: Database, chat_id: str, path: list[dict[str, Any]], payload: dict[str, Any],
                 storage: Any) -> None:
    """«Сохранить» a summary by hand: a new version with a lock, standing for the chapter's text as it is now."""
    placed = on_path(db, chat_id, path, _need(payload, "chapter_id"))
    text = _body(payload, "пересказ")
    previous = store.current_summaries(db, chat_id).get(placed.row["id"])
    digest = chapters.content_hash(path[placed.lo:placed.hi + 1])
    made = store.put_summary(db, chat_id, placed.row["id"], text, digest, by="hand", locked=True)
    store.put_chapters(db, [{**placed.row, "content_hash": digest}])
    chapters.log_text(storage, "Изменить пересказ", made, summary_of=placed.row["id"],
                      previous=previous["id"] if previous else None)


def summary_lock(db: Database, chat_id: str, path: list[dict[str, Any]], payload: dict[str, Any],
                 storage: Any) -> None:
    placed = on_path(db, chat_id, path, _need(payload, "chapter_id"))
    current = store.current_summaries(db, chat_id).get(placed.row["id"])
    if current is None:
        raise chapters.ChapterError("У этой главы ещё нет пересказа")
    locked = bool(payload.get("locked"))
    store.set_summary_lock(db, placed.row["id"], locked)
    chapters.log_text(storage, "Поставить замок" if locked else "Снять замок", "", summary_of=placed.row["id"],
                      lock_was=current["locked"])


def summary_restore(db: Database, chat_id: str, path: list[dict[str, Any]], payload: dict[str, Any],
                    storage: Any) -> None:
    """«Вернуть эту версию» of a summary."""
    row = store.get_text(db, _need(payload, "text_id"))
    if row is None or row["chat_id"] != chat_id or row["kind"] != "summary":
        raise chapters.ChapterError("Такой версии нет")
    on_path(db, chat_id, path, row["target"])
    previous = store.current_summaries(db, chat_id).get(row["target"])
    store.restore_summary(db, row["id"])
    chapters.log_text(storage, "Вернуть версию пересказа", "", summary_of=row["target"],
                      previous=previous["id"] if previous else None)


def summary_versions(db: Database, chat_id: str, chapter_id: str) -> list[dict[str, Any]]:
    rows = db.fetchall("SELECT id FROM p_memory_texts WHERE chat_id = ? AND kind = 'summary' AND target = ?",
                       (chat_id, chapter_id))
    mine = {r["id"] for r in rows}
    return [{k: v[k] for k in ("id", "text", "by", "locked", "current", "created_at")}
            for v in store.summary_versions(db, chapter_id) if v["id"] in mine]


def ask_resummarize(db: Database, chat_id: str, path: list[dict[str, Any]], chapter_id: str, storage: Any) -> None:
    """«Пересказать заново»: the job takes this chapter next, with «Пересказы глав» off too. Never over a lock."""
    placed = on_path(db, chat_id, path, chapter_id)
    current = store.current_summaries(db, chat_id).get(placed.row["id"])
    if current is not None and current["locked"]:
        raise chapters.ChapterError("Пересказ под замком: сначала снимите замок")
    if placed.row["state"] == "deleted":
        raise chapters.ChapterError("Глава удалена: сначала верните её")
    storage.set("force", sorted({*storage.get("force", []), placed.row["id"]}))


def plot_edit(db: Database, chat_id: str, path: list[dict[str, Any]], payload: dict[str, Any], storage: Any) -> None:
    """«Сюжет до сих пор» by hand: a new version of this branch (at the current one's anchor), locked."""
    text = _body(payload, "сюжет")
    current = store.plot_for_path(db, chat_id, {m["id"] for m in path})
    # not at the newest message: a regenerated or continued reply leaves it out of its own history (M5 fix of M3)
    anchor = current["anchor_id"] if current else chapters.fresh_anchor(path)
    made = store.put_plot(db, chat_id, text, anchor, current["made_from"] if current else "[]", by="hand",
                          locked=True)
    chapters.log_text(storage, "Изменить сюжет", made)


def plot_lock(db: Database, chat_id: str, path: list[dict[str, Any]], payload: dict[str, Any], storage: Any) -> None:
    current = store.plot_for_path(db, chat_id, {m["id"] for m in path})
    if current is None:
        raise chapters.ChapterError("Сюжета ещё нет")
    store.set_plot_lock(db, current["id"], bool(payload.get("locked")))


def plot_restore(db: Database, chat_id: str, path: list[dict[str, Any]], payload: dict[str, Any],
                 storage: Any) -> None:
    """«Вернуть эту версию» of the plot: a copy of it becomes the branch's newest version, locked."""
    row = next((r for r in store.plot_versions(db, chat_id, {m["id"] for m in path})
                if r["id"] == payload.get("text_id")), None)
    if row is None:
        raise chapters.ChapterError("Такой версии сюжета нет на этой ветке")
    made = store.put_plot(db, chat_id, row["text"], row["anchor_id"], row["made_from"], by="hand", locked=True)
    chapters.log_text(storage, "Вернуть версию сюжета", made)


def plot_versions(db: Database, chat_id: str, path: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{k: v[k] for k in ("id", "text", "by", "locked", "created_at")}
            for v in store.plot_versions(db, chat_id, {m["id"] for m in path})]
