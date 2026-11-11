"""Chapters on a branch: which belong to it, staleness by content hash, the deleted-message repair, and the hand
edits (split, merge, move a border, delete, restore) with a log for undo. ADR 0019 sections 3.1 and 3.2.

A chapter is stored by its first and last message id. It belongs to the open branch (the chat's active path) when
both are on it and the first is not after the last. So chapters before a fork are shared by every branch, and one
of another branch is silent with no extra rule.

`content_hash` covers the conversation messages of the chapter in path order: id, active swipe, text. A mismatch
makes the chapter `stale` (the text under it changed: an edit, another swipe, a message hidden or deleted), and it
goes back to `raw` by itself when the text is the same again. «Принять изменения» (`accept`) adopts the new hash.
The event handlers only make this quicker; the job checks the hash on every pass.

Every hand edit writes one step to `undo:<chat>` (plugin storage, the last 50): the chapter rows as they were and
the ids it created (or a text, M3-M4, a «Слово и нити» record, M5, or a mini-card, M6); `undo` puts it back.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from tavern.errors import AppError
from tavern.storage.db import Database
from tavern.util.ids import now_iso

from . import pieces, store

UNDO_KEEP = 50
STATES = ("raw", "summarized", "stale", "failed", "deleted")


class ChapterError(AppError):
    """A hand edit that cannot be made; the message is for the user."""

    code = "memory_chapter"
    status = 409


@dataclass
class Placed:
    """A chapter row on the open branch with where it stands in the path (indexes of its first and last message)."""

    row: dict[str, Any]
    lo: int
    hi: int


def index_of(path: list[dict[str, Any]]) -> dict[str, int]:
    return {m["id"]: i for i, m in enumerate(path)}


def placed(rows: list[dict[str, Any]], path: list[dict[str, Any]]) -> list[Placed]:
    """The chapters of `rows` that belong to the branch `path`, in story order."""
    index = index_of(path)
    out = [Placed(r, index[r["first_id"]], index[r["last_id"]]) for r in rows
           if r["first_id"] in index and r["last_id"] in index and index[r["first_id"]] <= index[r["last_id"]]]
    return sorted(out, key=lambda p: (p.lo, p.hi))


def content_hash(messages: list[dict[str, Any]]) -> str:
    digest = hashlib.sha256()
    for m in messages:
        if pieces.eligible(m):
            digest.update(f"{m['id']}|{m['active_swipe']}|{m['content']}\n".encode())
    return digest.hexdigest()[:32]


def participants(messages: list[dict[str, Any]], me_id: str = "") -> list[str]:
    """Who took part: the characters who wrote in it, and the user's persona (its id, else «user»)."""
    found: list[str] = []
    for m in messages:
        if not pieces.eligible(m):
            continue
        key = (me_id or "user") if m["role"] == "user" else str(m.get("character_id") or m.get("name") or "")
        if key and key not in found:
            found.append(key)
    return found


def eligible_in(path: list[dict[str, Any]], lo: int, hi: int) -> list[dict[str, Any]]:
    return [m for m in path[lo:hi + 1] if pieces.eligible(m)]


def number_of(m: dict[str, Any]) -> int:
    return int(m["depth"]) + 1


def fresh_anchor(path: list[dict[str, Any]]) -> str:
    """Where a hand-made version with nothing to follow is anchored: the message before the newest one. The newest
    may be the reply being regenerated or continued, which the reply's own history leaves out, and a version anchored
    there would be off the branch exactly then (M4; M5 fixed the plot's hand edit with it)."""
    if not path:
        raise ChapterError("В чате нет сообщений")
    return path[-2]["id"] if len(path) > 1 else path[-1]["id"]


# -- the open part ---------------------------------------------------------------------------------


def open_start(rows: list[Placed]) -> int:
    """Index in the path where the part without chapters begins (after the last chapter, deleted ones included)."""
    return max((p.hi for p in rows), default=-1) + 1


# -- staleness and cleanup -------------------------------------------------------------------------


def refresh(db: Database, chat_id: str, path: list[dict[str, Any]]) -> dict[str, int]:
    """One cheap pass: drop chapters whose ends are really gone, set each chapter's state from the hash and its
    summary. No model.

    `stale`: the text under the chapter changed (its hash is not the one stored), or its summary tells another
    text than the chapter has now (a split, a merge, a moved border). Otherwise `summarized` when it has a summary,
    `failed` when the last attempt to summarize it failed, else `raw`. It goes back by itself when the text is the
    same again."""
    rows = store.chapter_rows(db, chat_id)
    ends = [r[k] for r in rows for k in ("first_id", "last_id")]
    alive = store.existing_messages(db, list(set(ends)))
    gone = [r["id"] for r in rows if r["first_id"] not in alive or r["last_id"] not in alive]
    if gone:
        store.drop_texts_of(db, gone)
        store.delete_chapters(db, gone)
    summaries = store.current_summaries(db, chat_id)
    changed = []
    for p in placed([r for r in rows if r["id"] not in gone], path):
        if p.row["state"] == "deleted":
            continue
        summary = summaries.get(p.row["id"])
        same = content_hash(path[p.lo:p.hi + 1]) == p.row["content_hash"]
        if not same or (summary is not None and summary["made_from"] != p.row["content_hash"]):
            want = "stale"
        elif summary is not None:
            want = "summarized"
        else:
            want = "failed" if p.row.get("error") else "raw"
        if p.row["state"] != want:
            changed.append({**p.row, "state": want, "updated_at": now_iso()})
    if changed:
        store.put_chapters(db, changed)
    return {"dropped": len(gone), "changed": len(changed)}


def on_message_deleted(db: Database, chat_id: str, message: dict[str, Any], children: list[str],
                       parent_of: Any) -> int:
    """A deleted message that was the first or last of a chapter: move that end to the neighbour, so the chapter
    survives (the content hash then makes it stale). `parent_of(id)` gives a message's parent id (its chain after the
    delete is what leads from the chapter's last message to the moved-up children). A chapter left with no message
    is dropped. Returns how many chapters were touched."""
    gone_id = message["id"]
    touched = 0
    for row in store.chapter_rows(db, chat_id):
        if gone_id not in (row["first_id"], row["last_id"]):
            continue
        touched += 1
        if row["first_id"] == row["last_id"]:
            store.delete_chapters(db, [row["id"]])
        elif row["last_id"] == gone_id:
            parent = message.get("parent_id")
            if parent:
                store.put_chapters(db, [{**row, "last_id": parent, "updated_at": now_iso()}])
            else:
                store.delete_chapters(db, [row["id"]])
        else:  # the first one: the child of the deleted message that leads to the chapter's last message
            current, seen = row["last_id"], 0
            heir = None
            while current and seen < 5000:
                if current in children:
                    heir = current
                    break
                current = parent_of(current)
                seen += 1
            if heir:
                store.put_chapters(db, [{**row, "first_id": heir, "updated_at": now_iso()}])
            else:
                store.delete_chapters(db, [row["id"]])
    return touched


# -- the log for undo ------------------------------------------------------------------------------


def _log(storage: Any, op: str, before: list[dict[str, Any]], created: list[str], **texts: Any) -> None:
    steps = storage.get("undo", [])
    steps.append({"op": op, "before": before, "created": created, "at": now_iso(), **texts})
    storage.set("undo", steps[-UNDO_KEEP:])


def log_text(storage: Any, op: str, made: str, *, summary_of: str = "", previous: str | None = None,
             lock_was: bool | None = None) -> None:
    """A hand edit of a summary or of the plot, in the same undo log as the chapter edits: `made` is the text row
    it wrote (removed on undo); for a summary, `previous` is the version that was current (made current again)."""
    _log(storage, op, [], [], text={"made": made, "summary_of": summary_of, "previous": previous,
                                    "lock_was": lock_was})


def log_thread(storage: Any, op: str, *, before: dict[str, Any] | None = None, created: str = "",
               made_state: str = "") -> None:
    """A hand edit of a «Слово и нити» record, in the same undo log: the record as it was, the record it created, the
    status row it made (store.undo_thread reverses them)."""
    _log(storage, op, [], [], thread={"before": before, "created": created, "made_state": made_state})


def log_face(storage: Any, op: str, *, before: dict[str, Any] | None = None, version: str = "",
             merge: dict[str, Any] | None = None) -> None:
    """A hand edit of a mini-card (M6), in the same undo log of the chat it was made in: the face as it was, the
    version it wrote, or what a merge moved (store.undo_face reverses them)."""
    _log(storage, op, [], [], face={"before": before, "version": version, "merge": merge})


def undo_label(storage: Any) -> str:
    steps = storage.get("undo", [])
    return steps[-1]["op"] if steps else ""


def undo(db: Database, storage: Any) -> str:
    """Reverse the last hand edit. Returns its name; ChapterError when there is nothing to undo."""
    steps = storage.get("undo", [])
    if not steps:
        raise ChapterError("Отменять нечего")
    step = steps.pop()
    text = step.get("text")
    if text:
        _undo_text(db, text)
    if step.get("thread"):
        store.undo_thread(db, step["thread"])
    if step.get("face"):
        store.undo_face(db, step["face"])
    if step["created"]:
        store.delete_chapters(db, step["created"])
    if step["before"]:
        store.put_chapters(db, step["before"])
    storage.set("undo", steps)
    return step["op"]


def _undo_text(db: Database, text: dict[str, Any]) -> None:
    if text.get("made"):
        store.delete_text(db, text["made"])
    chapter_id = text.get("summary_of")
    if not chapter_id:
        return  # the plot: the newest version left on the branch is the current one again
    if text.get("previous"):
        try:
            store.restore_summary(db, text["previous"])
        except KeyError:
            pass  # that version was pruned meanwhile: the chapter just has no current summary
    elif text.get("lock_was") is not None:
        store.set_summary_lock(db, chapter_id, bool(text["lock_was"]))


# -- hand edits ------------------------------------------------------------------------------------


def _find(rows: list[Placed], chapter_id: str) -> Placed:
    for p in rows:
        if p.row["id"] == chapter_id:
            return p
    raise ChapterError("Эта глава не относится к открытой ветке чата")


def _rehash(row: dict[str, Any], path: list[dict[str, Any]], first: int, last: int, me_id: str,
            **changes: Any) -> dict[str, Any]:
    span = path[first:last + 1]
    return {**row, "first_id": path[first]["id"], "last_id": path[last]["id"], "content_hash": content_hash(span),
            "participants": participants(span, me_id), "state": "raw" if row["state"] != "deleted" else "deleted",
            "updated_at": now_iso(), **changes}


def _prev_eligible(path: list[dict[str, Any]], at: int, floor: int) -> int | None:
    for i in range(at - 1, floor - 1, -1):
        if pieces.eligible(path[i]):
            return i
    return None


def split(db: Database, storage: Any, chat_id: str, path: list[dict[str, Any]], message_id: str, me_id: str = "",
          ) -> list[dict[str, Any]]:
    """«Новая глава отсюда»: a border right before `message_id`. Inside a chapter it is cut in two; in the part
    without chapters the messages before it become a chapter. Returns the chapter rows that changed."""
    index = index_of(path)
    if message_id not in index:
        raise ChapterError("Это сообщение не на открытой ветке")
    at = index[message_id]
    if not pieces.eligible(path[at]):
        raise ChapterError("Граница ставится перед обычным сообщением диалога")
    rows = placed(store.chapter_rows(db, chat_id), path)
    inside = next((p for p in rows if p.lo <= at <= p.hi and p.row["state"] != "deleted"), None)
    if inside is not None:
        before = _prev_eligible(path, at, inside.lo)
        if before is None:
            raise ChapterError("Здесь уже начинается глава")
        old = inside.row
        left = _rehash(old, path, inside.lo, before, me_id, border_by="hand", reason=old["reason"])
        right = store.new_chapter(chat_id, path[at]["id"], path[inside.hi]["id"], content_hash(path[at:inside.hi + 1]),
                                  participants(path[at:inside.hi + 1], me_id), by="hand", reason="вручную")
        _log(storage, "Разрезать главу", [old], [right["id"]])
        store.put_chapters(db, [left, right])
        return [left, right]
    start = open_start(rows)
    if at < start:
        raise ChapterError("Это сообщение в удалённой главе: сначала верните её")
    before = _prev_eligible(path, at, start)
    if before is None:
        raise ChapterError("Перед этим сообщением ещё нет ни одного без главы")
    first = next(i for i in range(start, before + 1) if pieces.eligible(path[i]))
    made = store.new_chapter(chat_id, path[first]["id"], path[before]["id"], content_hash(path[first:before + 1]),
                             participants(path[first:before + 1], me_id), by="hand", reason="вручную")
    _log(storage, "Новая глава", [], [made["id"]])
    store.put_chapters(db, [made])
    return [made]


def merge(db: Database, storage: Any, chat_id: str, path: list[dict[str, Any]], chapter_id: str, me_id: str = "",
          ) -> dict[str, Any]:
    """«Склеить»: the chapter and the next one become one. Both must touch (no messages between them)."""
    rows = placed(store.chapter_rows(db, chat_id), path)
    this = _find(rows, chapter_id)
    after = [p for p in rows if p.lo > this.hi]
    if not after:
        raise ChapterError("После этой главы нет другой, чтобы склеить")
    following = after[0]
    if eligible_in(path, this.hi + 1, following.lo - 1):
        raise ChapterError("Между главами есть сообщения без главы")
    old = [this.row, following.row]
    merged = _rehash(this.row, path, this.lo, following.hi, me_id, border_by="hand", reason="склеена вручную",
                     state="raw" if "deleted" not in (this.row["state"], following.row["state"]) else "deleted")
    _log(storage, "Склеить главы", old, [])
    store.delete_chapters(db, [following.row["id"]])
    store.put_chapters(db, [merged])
    return merged


def move_border(db: Database, storage: Any, chat_id: str, path: list[dict[str, Any]], chapter_id: str,
                message_id: str, me_id: str = "") -> list[dict[str, Any]]:
    """«Сдвинуть границу»: the chapter now begins at `message_id`. The previous chapter (which must touch it) ends
    right before; the message lies in the previous chapter (the border moves back) or in this one (it moves on)."""
    index = index_of(path)
    if message_id not in index:
        raise ChapterError("Это сообщение не на открытой ветке")
    rows = placed(store.chapter_rows(db, chat_id), path)
    this = _find(rows, chapter_id)
    earlier = [p for p in rows if p.hi < this.lo]
    if not earlier or eligible_in(path, earlier[-1].hi + 1, this.lo - 1):
        raise ChapterError("Перед этой главой нет соседней главы: двигать нечего")
    prev = earlier[-1]
    at = index[message_id]
    if not pieces.eligible(path[at]) or not prev.lo < at <= this.hi:
        raise ChapterError("Новая граница должна быть внутри этой или предыдущей главы, но не в самом её начале")
    before = _prev_eligible(path, at, prev.lo)
    if before is None or at == this.lo:
        raise ChapterError("Граница уже здесь")
    new_prev = _rehash(prev.row, path, prev.lo, before, me_id, border_by="hand", reason=prev.row["reason"])
    new_this = _rehash(this.row, path, at, this.hi, me_id, border_by="hand", reason="граница сдвинута вручную")
    _log(storage, "Сдвинуть границу", [prev.row, this.row], [])
    store.put_chapters(db, [new_prev, new_this])
    return [new_prev, new_this]


def remove(db: Database, storage: Any, chat_id: str, path: list[dict[str, Any]], chapter_id: str) -> dict[str, Any]:
    """Delete a chapter (it stays in the list as «удалена» and can be brought back; its range is not re-cut)."""
    this = _find(placed(store.chapter_rows(db, chat_id), path), chapter_id)
    if this.row["state"] == "deleted":
        raise ChapterError("Глава уже удалена")
    row = {**this.row, "state": "deleted", "updated_at": now_iso()}
    _log(storage, "Удалить главу", [this.row], [])
    store.put_chapters(db, [row])
    return row


def restore(db: Database, storage: Any, chat_id: str, path: list[dict[str, Any]], chapter_id: str, me_id: str = "",
            ) -> dict[str, Any]:
    this = _find(placed(store.chapter_rows(db, chat_id), path), chapter_id)
    if this.row["state"] != "deleted":
        raise ChapterError("Эта глава не удалена")
    row = _rehash({**this.row, "state": "raw"}, path, this.lo, this.hi, me_id)
    _log(storage, "Вернуть главу", [this.row], [])
    store.put_chapters(db, [row])
    return row


def accept(db: Database, storage: Any, chat_id: str, path: list[dict[str, Any]], chapter_id: str, me_id: str = "",
           ) -> dict[str, Any]:
    """«Принять изменения»: the chapter's text changed under it; take it as it is now."""
    this = _find(placed(store.chapter_rows(db, chat_id), path), chapter_id)
    row = _rehash(this.row, path, this.lo, this.hi, me_id)
    _log(storage, "Принять изменения", [this.row], [])
    store.put_chapters(db, [row])
    store.restamp_summary(db, chapter_id, row["content_hash"])  # its summary stands for the text as it is now
    return row


def edit(db: Database, storage: Any, chat_id: str, path: list[dict[str, Any]], chapter_id: str, *,
         title: str | None = None, whole: bool | None = None) -> dict[str, Any]:
    this = _find(placed(store.chapter_rows(db, chat_id), path), chapter_id)
    changes: dict[str, Any] = {"updated_at": now_iso()}
    if title is not None:
        changes["title"] = title.strip()[:120]
    if whole is not None:
        changes["whole"] = bool(whole)
    row = {**this.row, **changes}
    _log(storage, "Изменить главу", [this.row], [])
    store.put_chapters(db, [row])
    return row
