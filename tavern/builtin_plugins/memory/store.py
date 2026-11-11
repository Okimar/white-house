"""SQL over the memory plugin's own tables (migrations/0001_memory.sql ... 0006_faces.sql). Nothing else touches them.

`db` is the app's `Database` (`api.ctx.db`). Vectors are stored normalized as `array('f')` bytes (a dot product is
then the cosine) next to the hash of the text they were made from and the model's name: a vector counts only when
both still match its piece. `VectorCache` keeps a chat's vectors in memory between replies, so scoring does not
read thousands of BLOBs every time (ADR 0019 risk R1).
"""

from __future__ import annotations

import json
from array import array
from typing import Any

from tavern.storage.db import Database
from tavern.util.ids import new_id, now_iso

from .pieces import Spec

PIECES, VECTORS, CHATS = "p_memory_pieces", "p_memory_vectors", "p_memory_chats"


# -- per-chat switch -------------------------------------------------------------------------------


def chat_row(db: Database, chat_id: str) -> dict[str, Any] | None:
    row = db.fetchone(f"SELECT chat_id, enabled, settings FROM {CHATS} WHERE chat_id = ?", (chat_id,))
    return None if row is None else {"chat_id": row["chat_id"], "enabled": bool(row["enabled"]),
                                     "settings": json.loads(row["settings"])}


def is_enabled(db: Database, chat_id: str) -> bool:
    row = db.fetchone(f"SELECT enabled FROM {CHATS} WHERE chat_id = ?", (chat_id,))
    return bool(row and row["enabled"])


def set_enabled(db: Database, chat_id: str, enabled: bool) -> None:
    db.execute(
        f"INSERT INTO {CHATS} (chat_id, enabled, updated_at) VALUES (?, ?, ?) "
        "ON CONFLICT(chat_id) DO UPDATE SET enabled = excluded.enabled, updated_at = excluded.updated_at",
        (chat_id, int(enabled), now_iso()))


# -- pieces ----------------------------------------------------------------------------------------


def pieces(db: Database, chat_id: str) -> list[dict[str, Any]]:
    """Every piece row of the chat (all branches): id, message id, part, hash, tokens, keywords."""
    rows = db.fetchall(
        f"SELECT id, first_id, part, text_hash, tokens, keywords FROM {PIECES} WHERE chat_id = ?", (chat_id,))
    return [{"id": r["id"], "message_id": r["first_id"], "part": r["part"], "hash": r["text_hash"],
             "tokens": r["tokens"], "keywords": json.loads(r["keywords"])} for r in rows]


def sync(db: Database, chat_id: str, wanted: list[Spec], path_ids: set[str]) -> dict[str, int]:
    """Make the pieces of the messages on `path_ids` equal `wanted`. Pieces of messages on other branches stay.
    Returns {added, changed, removed}. A changed piece keeps its id; its old vector goes stale by hash."""
    have = {(p["message_id"], p["part"]): p for p in pieces(db, chat_id) if p["message_id"] in path_ids}
    want = {(s.message_id, s.part): s for s in wanted}
    added = changed = removed = 0
    now = now_iso()
    with db.transaction() as conn:
        for key, piece in have.items():
            if key not in want:
                conn.execute(f"DELETE FROM {PIECES} WHERE id = ?", (piece["id"],))
                removed += 1
        for key, spec in want.items():
            piece = have.get(key)
            keywords = json.dumps(list(spec.keywords), ensure_ascii=False)
            if piece is None:
                conn.execute(
                    f"INSERT INTO {PIECES} (id, chat_id, first_id, last_id, part, text_hash, tokens, keywords, "
                    "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (new_id(), chat_id, spec.message_id, spec.message_id, spec.part, spec.hash, spec.tokens,
                     keywords, now))
                added += 1
            elif piece["hash"] != spec.hash:
                conn.execute(f"UPDATE {PIECES} SET text_hash = ?, tokens = ?, keywords = ? WHERE id = ?",
                             (spec.hash, spec.tokens, keywords, piece["id"]))
                changed += 1
    return {"added": added, "changed": changed, "removed": removed}


# -- vectors ---------------------------------------------------------------------------------------


def normalized(vector: list[float]) -> array:
    norm = sum(x * x for x in vector) ** 0.5
    return array("f", [x / norm for x in vector] if norm else vector)


def current_vector_ids(db: Database, chat_id: str, model: str) -> dict[str, str]:
    """item id -> text hash of the stored vector, for one model (no BLOBs read)."""
    rows = db.fetchall(f"SELECT item_id, text_hash FROM {VECTORS} WHERE chat_id = ? AND model = ?",
                       (chat_id, model))
    return {r["item_id"]: r["text_hash"] for r in rows}


def save_vectors(db: Database, chat_id: str, model: str, items: list[tuple[str, str, list[float]]]) -> None:
    """items: (piece id, text hash, vector). One transaction: a batch is kept whole or not at all."""
    with db.transaction() as conn:
        for item_id, digest, vector in items:
            conn.execute(
                f"INSERT INTO {VECTORS} (item_id, chat_id, model, text_hash, vector) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(item_id, model) DO UPDATE SET text_hash = excluded.text_hash, "
                "vector = excluded.vector, chat_id = excluded.chat_id",
                (item_id, chat_id, model, digest, normalized(vector).tobytes()))


def vector_count(db: Database, chat_id: str, model: str) -> int:
    row = db.fetchone(f"SELECT COUNT(*) AS n FROM {VECTORS} WHERE chat_id = ? AND model = ?", (chat_id, model))
    return int(row["n"])


class VectorCache:
    """A chat's vectors in memory: item id -> (text hash, array('f')). Reloaded when the table changed
    (count and the newest rowid differ)."""

    def __init__(self) -> None:
        self._data: dict[tuple[str, str], tuple[tuple[int, int], dict[str, tuple[str, array]]]] = {}

    def get(self, db: Database, chat_id: str, model: str) -> dict[str, tuple[str, array]]:
        stamp = db.fetchone(f"SELECT COUNT(*) AS n, COALESCE(MAX(rowid), 0) AS top FROM {VECTORS} "
                            "WHERE chat_id = ? AND model = ?", (chat_id, model))
        key = (int(stamp["n"]), int(stamp["top"]))
        cached = self._data.get((chat_id, model))
        if cached is not None and cached[0] == key:
            return cached[1]
        loaded: dict[str, tuple[str, array]] = {}
        for row in db.fetchall(f"SELECT item_id, text_hash, vector FROM {VECTORS} WHERE chat_id = ? AND model = ?",
                               (chat_id, model)):
            vector = array("f")
            vector.frombytes(row["vector"])
            loaded[row["item_id"]] = (row["text_hash"], vector)
        self._data[(chat_id, model)] = (key, loaded)
        return loaded

    def clear(self) -> None:
        self._data.clear()


# -- chapters (migrations/0002_chapters.sql) -------------------------------------------------------

CHAPTERS = "p_memory_chapters"
CHAPTER_FIELDS = ("id", "chat_id", "first_id", "last_id", "title", "state", "content_hash", "participants", "whole",
                  "border_by", "reason", "tries", "created_at", "updated_at", "error", "failed_at")


def _chapter(row: Any) -> dict[str, Any]:
    out = {key: row[key] for key in CHAPTER_FIELDS}
    out["participants"] = json.loads(out["participants"])
    out["whole"] = bool(out["whole"])
    return out


def chapter_rows(db: Database, chat_id: str) -> list[dict[str, Any]]:
    """Every chapter row of the chat, all branches (which of them belong to the open branch: chapters.placed)."""
    rows = db.fetchall(f"SELECT {', '.join(CHAPTER_FIELDS)} FROM {CHAPTERS} WHERE chat_id = ? "
                       "ORDER BY created_at, id", (chat_id,))
    return [_chapter(r) for r in rows]


def get_chapter(db: Database, chapter_id: str) -> dict[str, Any] | None:
    row = db.fetchone(f"SELECT {', '.join(CHAPTER_FIELDS)} FROM {CHAPTERS} WHERE id = ?", (chapter_id,))
    return None if row is None else _chapter(row)


def put_chapters(db: Database, rows: list[dict[str, Any]]) -> None:
    """Insert or update whole chapter rows (a new chapter, an edit, an undo): one transaction. An upsert, not
    REPLACE: replacing would delete the row and cascade away its sticky hits."""
    sets = ", ".join(f"{k} = excluded.{k}" for k in CHAPTER_FIELDS if k != "id")
    with db.transaction() as conn:
        for row in rows:
            values = [json.dumps(row[k], ensure_ascii=False) if k == "participants" else
                      (int(row[k]) if k == "whole" else row.get(k, "" if k in ("error", "failed_at") else None))
                      for k in CHAPTER_FIELDS]
            conn.execute(
                f"INSERT INTO {CHAPTERS} ({', '.join(CHAPTER_FIELDS)}) VALUES ({', '.join('?' * len(CHAPTER_FIELDS))}) "
                f"ON CONFLICT(id) DO UPDATE SET {sets}", values)


def delete_chapters(db: Database, ids: list[str]) -> None:
    with db.transaction() as conn:
        for chapter_id in ids:
            conn.execute(f"DELETE FROM {CHAPTERS} WHERE id = ?", (chapter_id,))


def new_chapter(chat_id: str, first_id: str, last_id: str, content_hash: str, participants: list[str], *,
                by: str = "script", reason: str = "", title: str = "") -> dict[str, Any]:
    now = now_iso()
    return {"id": new_id(), "chat_id": chat_id, "first_id": first_id, "last_id": last_id, "title": title,
            "state": "raw", "content_hash": content_hash, "participants": participants, "whole": False,
            "border_by": by, "reason": reason, "tries": 0, "created_at": now, "updated_at": now,
            "error": "", "failed_at": ""}


def existing_messages(db: Database, ids: list[str]) -> set[str]:
    """Which of these message ids are still in the database."""
    if not ids:
        return set()
    marks = ", ".join("?" * len(ids))
    return {r["id"] for r in db.fetchall(f"SELECT id FROM messages WHERE id IN ({marks})", tuple(ids))}


# -- summaries and the plot (migrations/0003_summaries.sql) -----------------------------------------

TEXTS, HITS, MEMBERS = "p_memory_texts", "p_memory_hits", "p_memory_members"
TEXT_FIELDS = "id, kind, target, anchor_id, made_from, text, made_by, locked, is_current, created_at"
KEEP_VERSIONS = 20  # older versions of one summary (or one chat's plot) are dropped past this


def _text(row: Any) -> dict[str, Any]:
    return {"id": row["id"], "kind": row["kind"], "target": row["target"], "anchor_id": row["anchor_id"],
            "made_from": row["made_from"], "text": row["text"], "by": row["made_by"], "locked": bool(row["locked"]),
            "current": bool(row["is_current"]), "created_at": row["created_at"]}


def current_summaries(db: Database, chat_id: str) -> dict[str, dict[str, Any]]:
    """chapter id -> its current summary row (text, made_from hash, locked, by)."""
    rows = db.fetchall(f"SELECT {TEXT_FIELDS} FROM {TEXTS} WHERE chat_id = ? AND kind = 'summary' AND is_current = 1",
                       (chat_id,))
    return {r["target"]: _text(r) for r in rows}


def summary_versions(db: Database, chapter_id: str) -> list[dict[str, Any]]:
    """Every version of a chapter's summary, newest first."""
    rows = db.fetchall(f"SELECT {TEXT_FIELDS} FROM {TEXTS} WHERE kind = 'summary' AND target = ? "
                       "ORDER BY created_at DESC, rowid DESC", (chapter_id,))
    return [_text(r) for r in rows]


def get_text(db: Database, text_id: str) -> dict[str, Any] | None:
    row = db.fetchone(f"SELECT {TEXT_FIELDS}, chat_id FROM {TEXTS} WHERE id = ?", (text_id,))
    return None if row is None else {**_text(row), "chat_id": row["chat_id"]}


def put_summary(db: Database, chat_id: str, chapter_id: str, text: str, made_from: str, *, by: str,
                locked: bool = False, title: str | None = None) -> str:
    """A new current version of a chapter's summary (the old one stays as a version). Clears the chapter's failure
    mark; `title` (the model's) is set only when the chapter has none yet."""
    text_id = new_id()
    with db.transaction() as conn:
        conn.execute(f"UPDATE {TEXTS} SET is_current = 0 WHERE kind = 'summary' AND target = ?", (chapter_id,))
        conn.execute(f"INSERT INTO {TEXTS} (id, chat_id, kind, target, anchor_id, made_from, text, made_by, locked, "
                     "is_current, created_at) VALUES (?, ?, 'summary', ?, NULL, ?, ?, ?, ?, 1, ?)",
                     (text_id, chat_id, chapter_id, made_from, text, by, int(locked), now_iso()))
        conn.execute(f"UPDATE {CHAPTERS} SET tries = 0, error = '', failed_at = '' WHERE id = ?", (chapter_id,))
        if title:
            conn.execute(f"UPDATE {CHAPTERS} SET title = ? WHERE id = ? AND title = ''", (title[:120], chapter_id))
        conn.execute(f"DELETE FROM {TEXTS} WHERE kind = 'summary' AND target = ? AND is_current = 0 AND id NOT IN "
                     f"(SELECT id FROM {TEXTS} WHERE kind = 'summary' AND target = ? "
                     f"ORDER BY created_at DESC, rowid DESC LIMIT {KEEP_VERSIONS})", (chapter_id, chapter_id))
    return text_id


def restore_summary(db: Database, text_id: str) -> dict[str, Any]:
    """«Вернуть эту версию»: the version becomes the current one (it keeps its own lock flag)."""
    row = get_text(db, text_id)
    if row is None or row["kind"] != "summary":
        raise KeyError(text_id)
    with db.transaction() as conn:
        conn.execute(f"UPDATE {TEXTS} SET is_current = 0 WHERE kind = 'summary' AND target = ?", (row["target"],))
        conn.execute(f"UPDATE {TEXTS} SET is_current = 1 WHERE id = ?", (text_id,))
    return row


def set_summary_lock(db: Database, chapter_id: str, locked: bool) -> None:
    db.execute(f"UPDATE {TEXTS} SET locked = ? WHERE kind = 'summary' AND target = ? AND is_current = 1",
               (int(locked), chapter_id))


def restamp_summary(db: Database, chapter_id: str, made_from: str) -> None:
    """«Принять изменения»: the current summary now stands for the chapter's text as it is."""
    db.execute(f"UPDATE {TEXTS} SET made_from = ? WHERE kind = 'summary' AND target = ? AND is_current = 1",
               (made_from, chapter_id))


def delete_text(db: Database, text_id: str) -> None:
    db.execute(f"DELETE FROM {TEXTS} WHERE id = ?", (text_id,))


def plot_versions(db: Database, chat_id: str, path_ids: set[str]) -> list[dict[str, Any]]:
    """The plot versions of a branch (anchored on it), newest first: the first one is the branch's plot."""
    return [r for r in plot_rows(db, chat_id) if r["anchor_id"] in path_ids]


def drop_texts_of(db: Database, chapter_ids: list[str]) -> None:
    with db.transaction() as conn:
        for chapter_id in chapter_ids:
            conn.execute(f"DELETE FROM {TEXTS} WHERE kind = 'summary' AND target = ?", (chapter_id,))


def mark_failed(db: Database, chapter_id: str, error: str) -> None:
    db.execute(f"UPDATE {CHAPTERS} SET tries = tries + 1, error = ?, failed_at = ?, state = 'failed' WHERE id = ?",
               ((error or "ошибка")[:300], now_iso(), chapter_id))


def plot_rows(db: Database, chat_id: str) -> list[dict[str, Any]]:
    """Every version of the chat's plot (all branches), newest first."""
    rows = db.fetchall(f"SELECT {TEXT_FIELDS} FROM {TEXTS} WHERE chat_id = ? AND kind = 'plot' "
                       "ORDER BY created_at DESC, rowid DESC", (chat_id,))
    return [_text(r) for r in rows]


def plot_for_path(db: Database, chat_id: str, path_ids: set[str]) -> dict[str, Any] | None:
    """The plot of a branch: the newest version anchored at a message of the branch."""
    return next((r for r in plot_rows(db, chat_id) if r["anchor_id"] in path_ids), None)


def put_plot(db: Database, chat_id: str, text: str, anchor_id: str, made_from: str, *, by: str,
             locked: bool = False) -> str:
    text_id = new_id()
    with db.transaction() as conn:
        conn.execute(f"INSERT INTO {TEXTS} (id, chat_id, kind, target, anchor_id, made_from, text, made_by, locked, "
                     "is_current, created_at) VALUES (?, ?, 'plot', '', ?, ?, ?, ?, ?, 1, ?)",
                     (text_id, chat_id, anchor_id, made_from, text, by, int(locked), now_iso()))
        conn.execute(f"DELETE FROM {TEXTS} WHERE chat_id = ? AND kind = 'plot' AND id NOT IN "
                     f"(SELECT id FROM {TEXTS} WHERE chat_id = ? AND kind = 'plot' "
                     f"ORDER BY created_at DESC, rowid DESC LIMIT {KEEP_VERSIONS * 2})", (chat_id, chat_id))
    return text_id


def set_plot_lock(db: Database, text_id: str, locked: bool) -> None:
    db.execute(f"UPDATE {TEXTS} SET locked = ? WHERE id = ? AND kind = 'plot'", (int(locked), text_id))


# -- sticky hits -----------------------------------------------------------------------------------


def hits(db: Database, chat_id: str) -> dict[str, tuple[int, str]]:
    rows = db.fetchall(f"SELECT chapter_id, hit_gen, what FROM {HITS} WHERE chat_id = ?", (chat_id,))
    return {r["chapter_id"]: (int(r["hit_gen"]), r["what"]) for r in rows}


def put_hits(db: Database, chat_id: str, items: list[tuple[str, int, str]]) -> None:
    with db.transaction() as conn:
        for chapter_id, gen, what in items:
            conn.execute(f"INSERT INTO {HITS} (chat_id, chapter_id, hit_gen, what) VALUES (?, ?, ?, ?) "
                         "ON CONFLICT(chat_id, chapter_id) DO UPDATE SET hit_gen = excluded.hit_gen, "
                         "what = excluded.what", (chat_id, chapter_id, gen, what))


def next_generation(db: Database, chat_id: str) -> int:
    """The memory's own reply counter of the chat (kept in the chat row's settings): one more reply."""
    row = chat_row(db, chat_id)
    settings = dict(row["settings"]) if row else {}
    settings["gen"] = int(settings.get("gen", 0)) + 1
    db.execute(f"UPDATE {CHATS} SET settings = ? WHERE chat_id = ?", (json.dumps(settings), chat_id))
    return int(settings["gen"])


# -- members ---------------------------------------------------------------------------------------


def member_flags(db: Database, chat_id: str) -> dict[str, dict[str, bool]]:
    rows = db.fetchall(f"SELECT character_id, remembers_all, no_layer FROM {MEMBERS} WHERE chat_id = ?", (chat_id,))
    return {r["character_id"]: {"remembers_all": bool(r["remembers_all"]), "no_layer": bool(r["no_layer"])}
            for r in rows}


def set_member(db: Database, chat_id: str, character_id: str, *, remembers_all: bool | None = None,
               no_layer: bool | None = None) -> None:
    now = member_flags(db, chat_id).get(character_id, {"remembers_all": False, "no_layer": False})
    flags = {"remembers_all": now["remembers_all"] if remembers_all is None else remembers_all,
             "no_layer": now["no_layer"] if no_layer is None else no_layer}
    db.execute(f"INSERT INTO {MEMBERS} (chat_id, character_id, remembers_all, no_layer) VALUES (?, ?, ?, ?) "
               "ON CONFLICT(chat_id, character_id) DO UPDATE SET remembers_all = excluded.remembers_all, "
               "no_layer = excluded.no_layer",
               (chat_id, character_id, int(flags["remembers_all"]), int(flags["no_layer"])))


# -- «Развитие» layers and «Сейчас:» lines (migrations/0004_layers.sql) ------------------------------

LAYER_RUNS = "p_memory_layer_runs"
ANCHORED = ("development", "now_line")


def anchored_rows(db: Database, chat_id: str, kind: str, target: str | None = None) -> list[dict[str, Any]]:
    """Every version of a kind (all branches), newest first; one target only when given."""
    where, args = "chat_id = ? AND kind = ?", [chat_id, kind]
    if target is not None:
        where += " AND target = ?"
        args.append(target)
    rows = db.fetchall(f"SELECT {TEXT_FIELDS} FROM {TEXTS} WHERE {where} ORDER BY created_at DESC, rowid DESC",
                       tuple(args))
    return [_text(r) for r in rows]


def anchored_for_path(db: Database, chat_id: str, kind: str, path_ids: set[str]) -> dict[str, dict[str, Any]]:
    """target -> the branch's version: the newest one anchored at a message of the branch (ADR 3.1)."""
    out: dict[str, dict[str, Any]] = {}
    for row in anchored_rows(db, chat_id, kind):
        if row["anchor_id"] in path_ids and row["target"] not in out:
            out[row["target"]] = row
    return out


def anchored_versions(db: Database, chat_id: str, kind: str, target: str, path_ids: set[str]) -> list[dict[str, Any]]:
    """The versions of one target on a branch, newest first: the first one is the branch's."""
    return [r for r in anchored_rows(db, chat_id, kind, target) if r["anchor_id"] in path_ids]


def put_anchored(db: Database, chat_id: str, kind: str, target: str, text: str, anchor_id: str, *, by: str,
                 locked: bool = False, made_from: str = "") -> str:
    """A new version of a layer or a line, anchored at `anchor_id`. Older versions of the target past
    2 x KEEP_VERSIONS (all branches together) are dropped."""
    if kind not in ANCHORED:
        raise ValueError(kind)
    text_id = new_id()
    with db.transaction() as conn:
        conn.execute(f"INSERT INTO {TEXTS} (id, chat_id, kind, target, anchor_id, made_from, text, made_by, locked, "
                     "is_current, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)",
                     (text_id, chat_id, kind, target, anchor_id, made_from, text, by, int(locked), now_iso()))
        conn.execute(f"DELETE FROM {TEXTS} WHERE chat_id = ? AND kind = ? AND target = ? AND id NOT IN "
                     f"(SELECT id FROM {TEXTS} WHERE chat_id = ? AND kind = ? AND target = ? "
                     f"ORDER BY created_at DESC, rowid DESC LIMIT {KEEP_VERSIONS * 2})",
                     (chat_id, kind, target, chat_id, kind, target))
    return text_id


def set_text_lock(db: Database, text_id: str, locked: bool) -> None:
    db.execute(f"UPDATE {TEXTS} SET locked = ? WHERE id = ?", (int(locked), text_id))


def layer_runs(db: Database, chat_id: str) -> dict[str, dict[str, Any]]:
    """chapter id -> {state, error, tries, failed_at} of the development step."""
    rows = db.fetchall(f"SELECT chapter_id, state, error, tries, updated_at FROM {LAYER_RUNS} WHERE chat_id = ?",
                       (chat_id,))
    return {r["chapter_id"]: {"state": r["state"], "error": r["error"], "tries": int(r["tries"]),
                              "failed_at": r["updated_at"] if r["state"] == "failed" else ""} for r in rows}


def put_layer_run(db: Database, chat_id: str, chapter_id: str, state: str, error: str = "") -> None:
    """The development step of a chapter is done, or failed once more (tries + 1)."""
    db.execute(
        f"INSERT INTO {LAYER_RUNS} (chapter_id, chat_id, state, error, tries, updated_at) VALUES (?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(chapter_id) DO UPDATE SET state = excluded.state, error = excluded.error, "
        f"tries = CASE WHEN excluded.state = 'failed' THEN {LAYER_RUNS}.tries + 1 ELSE 0 END, "
        "updated_at = excluded.updated_at",
        (chapter_id, chat_id, state, (error or "")[:300], 1 if state == "failed" else 0, now_iso()))


# -- «Слово и нити» (migrations/0005_threads.sql) ------------------------------------------------------

THREADS, THREAD_STATES, THREAD_RUNS = "p_memory_threads", "p_memory_thread_states", "p_memory_thread_runs"
FACE_RUNS = "p_memory_face_runs"  # M6 (0006_faces.sql): which chapters the faces step read
THREAD_FIELDS = ("id", "chat_id", "kind", "who", "to_whom", "knows", "quote", "message_id", "anchor_id", "chapter_id",
                 "importance", "keywords", "made_by", "locked", "deleted", "created_at", "updated_at")
STATE_FIELDS = "id, thread_id, anchor_id, status, knows, note, message_id, made_by, created_at"


def _thread(row: Any) -> dict[str, Any]:
    out = {key: row[key] for key in THREAD_FIELDS}
    out["knows"] = json.loads(out["knows"])
    out["keywords"] = json.loads(out["keywords"])
    out["locked"] = bool(out["locked"])
    out["deleted"] = bool(out["deleted"])
    return out


def thread_rows(db: Database, chat_id: str) -> list[dict[str, Any]]:
    """Every record of the chat (all branches, deleted ones too), oldest first."""
    rows = db.fetchall(f"SELECT {', '.join(THREAD_FIELDS)} FROM {THREADS} WHERE chat_id = ? ORDER BY created_at, rowid",
                       (chat_id,))
    return [_thread(r) for r in rows]


def get_thread(db: Database, thread_id: str) -> dict[str, Any] | None:
    row = db.fetchone(f"SELECT {', '.join(THREAD_FIELDS)} FROM {THREADS} WHERE id = ?", (thread_id,))
    return None if row is None else _thread(row)


def put_thread(db: Database, row: dict[str, Any]) -> None:
    """Insert or update a whole record row (new, hand edit, undo). An upsert: replacing would cascade its states."""
    sets = ", ".join(f"{k} = excluded.{k}" for k in THREAD_FIELDS if k != "id")
    values = [json.dumps(row[k], ensure_ascii=False) if k in ("knows", "keywords") else
              (int(row[k]) if k in ("locked", "deleted", "importance") else row[k]) for k in THREAD_FIELDS]
    db.execute(f"INSERT INTO {THREADS} ({', '.join(THREAD_FIELDS)}) VALUES ({', '.join('?' * len(THREAD_FIELDS))}) "
               f"ON CONFLICT(id) DO UPDATE SET {sets}", values)


def delete_thread(db: Database, thread_id: str) -> None:
    db.execute(f"DELETE FROM {THREADS} WHERE id = ?", (thread_id,))


def new_thread(chat_id: str, kind: str, who: str, quote: str, anchor_id: str, *, to_whom: str = "",
               knows: list[str] | None = None, message_id: str | None = None, chapter_id: str = "", importance: int = 2,
               keywords: list[str] | None = None, by: str = "auto", locked: bool = False) -> dict[str, Any]:
    now = now_iso()
    return {"id": new_id(), "chat_id": chat_id, "kind": kind, "who": who, "to_whom": to_whom, "knows": knows or [],
            "quote": quote, "message_id": message_id, "anchor_id": anchor_id, "chapter_id": chapter_id,
            "importance": importance, "keywords": keywords or [], "made_by": by, "locked": locked, "deleted": False,
            "created_at": now, "updated_at": now}


def _state(row: Any) -> dict[str, Any]:
    return {"id": row["id"], "thread_id": row["thread_id"], "anchor_id": row["anchor_id"], "status": row["status"],
            "knows": json.loads(row["knows"]), "note": row["note"], "message_id": row["message_id"],
            "by": row["made_by"], "created_at": row["created_at"]}


def thread_states(db: Database, chat_id: str) -> list[dict[str, Any]]:
    """Every status row of the chat (all branches), newest first."""
    rows = db.fetchall(f"SELECT {STATE_FIELDS} FROM {THREAD_STATES} WHERE chat_id = ? "
                       "ORDER BY created_at DESC, rowid DESC", (chat_id,))
    return [_state(r) for r in rows]


def states_for_path(db: Database, chat_id: str, path_ids: set[str]) -> dict[str, list[dict[str, Any]]]:
    """thread id -> its status rows anchored on the branch, newest first (the first one is the branch's status)."""
    out: dict[str, list[dict[str, Any]]] = {}
    for row in thread_states(db, chat_id):
        if row["anchor_id"] in path_ids:
            out.setdefault(row["thread_id"], []).append(row)
    return out


def put_state(db: Database, chat_id: str, thread_id: str, anchor_id: str, status: str, knows: list[str], *,
              by: str, note: str = "", message_id: str | None = None) -> str:
    state_id = new_id()
    db.execute(f"INSERT INTO {THREAD_STATES} (id, thread_id, chat_id, anchor_id, status, knows, note, message_id, "
               "made_by, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
               (state_id, thread_id, chat_id, anchor_id, status, json.dumps(knows, ensure_ascii=False), note[:300],
                message_id, by, now_iso()))
    return state_id


def delete_state(db: Database, state_id: str) -> None:
    db.execute(f"DELETE FROM {THREAD_STATES} WHERE id = ?", (state_id,))


def undo_thread(db: Database, step: dict[str, Any]) -> None:
    """Reverse one hand edit of a record (chapters.undo): the status row it made, the record it created, the record
    as it was before."""
    if step.get("made_state"):
        delete_state(db, step["made_state"])
    if step.get("created"):
        delete_thread(db, step["created"])
    if step.get("before"):
        put_thread(db, step["before"])


def runs(db: Database, table: str, chat_id: str) -> dict[str, dict[str, Any]]:
    """chapter id -> {state, error, tries, failed_at} of a per-chapter step (`LAYER_RUNS`, `THREAD_RUNS`,
    `FACE_RUNS`)."""
    if table not in (LAYER_RUNS, THREAD_RUNS, FACE_RUNS):
        raise ValueError(table)
    rows = db.fetchall(f"SELECT chapter_id, state, error, tries, updated_at FROM {table} WHERE chat_id = ?", (chat_id,))
    return {r["chapter_id"]: {"state": r["state"], "error": r["error"], "tries": int(r["tries"]),
                              "failed_at": r["updated_at"] if r["state"] == "failed" else ""} for r in rows}


def put_run(db: Database, table: str, chat_id: str, chapter_id: str, state: str, error: str = "") -> None:
    """A per-chapter step of a chapter is done, or failed once more (tries + 1)."""
    if table not in (LAYER_RUNS, THREAD_RUNS, FACE_RUNS):
        raise ValueError(table)
    db.execute(
        f"INSERT INTO {table} (chapter_id, chat_id, state, error, tries, updated_at) VALUES (?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(chapter_id) DO UPDATE SET state = excluded.state, error = excluded.error, "
        f"tries = CASE WHEN excluded.state = 'failed' THEN {table}.tries + 1 ELSE 0 END, "
        "updated_at = excluded.updated_at",
        (chapter_id, chat_id, state, (error or "")[:300], 1 if state == "failed" else 0, now_iso()))


# -- «Лица»: mini-cards of side characters (migrations/0006_faces.sql) ---------------------------------

FACES, FACE_VERSIONS, FACE_CHATS = "p_memory_faces", "p_memory_face_versions", "p_memory_face_chats"
FACE_FIELDS = ("id", "name", "aliases", "card", "favourite", "made_by", "locked", "deleted", "merged_into",
               "character_id", "origin_chat_id", "created_at", "updated_at")
FACE_PREFIX = "face:"  # a face as a target of layers and records: 'face:<id>'


def _face(row: Any) -> dict[str, Any]:
    out = {key: row[key] for key in FACE_FIELDS}
    out["aliases"] = json.loads(out["aliases"])
    for key in ("favourite", "locked", "deleted"):
        out[key] = bool(out[key])
    return out


def face_rows(db: Database, *, merged: bool = False) -> list[dict[str, Any]]:
    """Every face of the app, oldest first; merged ones only with `merged`."""
    where = "" if merged else "WHERE merged_into = ''"
    rows = db.fetchall(f"SELECT {', '.join(FACE_FIELDS)} FROM {FACES} {where} ORDER BY created_at, rowid")
    return [_face(r) for r in rows]


def get_face(db: Database, face_id: str) -> dict[str, Any] | None:
    row = db.fetchone(f"SELECT {', '.join(FACE_FIELDS)} FROM {FACES} WHERE id = ?", (face_id,))
    return None if row is None else _face(row)


def put_face(db: Database, row: dict[str, Any], *, version_by: str | None = None) -> str:
    """Insert or update a whole face row (an upsert: replacing would cascade its links and versions). With
    `version_by`, its name, aliases and card are also kept as a new version. Returns the version id ('' = none)."""
    sets = ", ".join(f"{k} = excluded.{k}" for k in FACE_FIELDS if k != "id")
    values = [json.dumps(row[k], ensure_ascii=False) if k == "aliases" else
              (int(row[k]) if k in ("favourite", "locked", "deleted") else row[k]) for k in FACE_FIELDS]
    version_id = ""
    with db.transaction() as conn:
        conn.execute(f"INSERT INTO {FACES} ({', '.join(FACE_FIELDS)}) VALUES ({', '.join('?' * len(FACE_FIELDS))}) "
                     f"ON CONFLICT(id) DO UPDATE SET {sets}", values)
        if version_by:
            version_id = new_id()
            conn.execute(f"INSERT INTO {FACE_VERSIONS} (id, face_id, name, aliases, card, made_by, created_at) "
                         "VALUES (?, ?, ?, ?, ?, ?, ?)",
                         (version_id, row["id"], row["name"], json.dumps(row["aliases"], ensure_ascii=False),
                          row["card"], version_by, now_iso()))
            conn.execute(f"DELETE FROM {FACE_VERSIONS} WHERE face_id = ? AND id NOT IN (SELECT id FROM {FACE_VERSIONS} "
                         f"WHERE face_id = ? ORDER BY created_at DESC, rowid DESC LIMIT {KEEP_VERSIONS})",
                         (row["id"], row["id"]))
    return version_id


def new_face(name: str, card: str, *, aliases: list[str] | None = None, by: str = "auto", origin: str = "",
             locked: bool = False) -> dict[str, Any]:
    now = now_iso()
    return {"id": new_id(), "name": name, "aliases": aliases or [], "card": card, "favourite": False, "made_by": by,
            "locked": locked, "deleted": False, "merged_into": "", "character_id": "", "origin_chat_id": origin,
            "created_at": now, "updated_at": now}


def delete_face(db: Database, face_id: str) -> None:
    db.execute(f"DELETE FROM {FACES} WHERE id = ?", (face_id,))


def face_versions(db: Database, face_id: str) -> list[dict[str, Any]]:
    """Every version of a face, newest first (the first one is how it is now)."""
    rows = db.fetchall(f"SELECT id, name, aliases, card, made_by, created_at FROM {FACE_VERSIONS} WHERE face_id = ? "
                       "ORDER BY created_at DESC, rowid DESC", (face_id,))
    return [{"id": r["id"], "name": r["name"], "aliases": json.loads(r["aliases"]), "card": r["card"],
             "by": r["made_by"], "created_at": r["created_at"]} for r in rows]


def delete_face_version(db: Database, version_id: str) -> None:
    db.execute(f"DELETE FROM {FACE_VERSIONS} WHERE id = ?", (version_id,))


def face_links(db: Database, *, chat_id: str | None = None, face_id: str | None = None) -> list[dict[str, Any]]:
    """Links of faces to chats (one chat's, or one face's)."""
    where: list[str] = []
    args: list[str] = []
    if chat_id is not None:
        where.append("chat_id = ?")
        args.append(chat_id)
    if face_id is not None:
        where.append("face_id = ?")
        args.append(face_id)
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    rows = db.fetchall(f"SELECT face_id, chat_id, first_message_id, in_use, created_at FROM {FACE_CHATS} {clause} "
                       "ORDER BY created_at, rowid", tuple(args))
    return [{"face_id": r["face_id"], "chat_id": r["chat_id"], "first_message_id": r["first_message_id"],
             "in_use": bool(r["in_use"]), "created_at": r["created_at"]} for r in rows]


def link_face(db: Database, face_id: str, chat_id: str, first_message_id: str | None = None, *,
              in_use: bool | None = None) -> None:
    """The face appeared in the chat (a link already there stays as it is); `in_use` is set when given."""
    db.execute(f"INSERT INTO {FACE_CHATS} (face_id, chat_id, first_message_id, in_use, created_at) "
               "VALUES (?, ?, ?, ?, ?) ON CONFLICT(face_id, chat_id) DO NOTHING",
               (face_id, chat_id, first_message_id, 1 if in_use is None else int(in_use), now_iso()))
    if in_use is not None:
        db.execute(f"UPDATE {FACE_CHATS} SET in_use = ? WHERE face_id = ? AND chat_id = ?",
                   (int(in_use), face_id, chat_id))


def put_links(db: Database, links: list[dict[str, Any]]) -> None:
    """Put link rows back as they were (undo of a merge)."""
    with db.transaction() as conn:
        for link in links:
            conn.execute(f"INSERT INTO {FACE_CHATS} (face_id, chat_id, first_message_id, in_use, created_at) "
                         "VALUES (?, ?, ?, ?, ?) ON CONFLICT(face_id, chat_id) DO UPDATE SET "
                         "first_message_id = excluded.first_message_id, in_use = excluded.in_use",
                         (link["face_id"], link["chat_id"], link["first_message_id"], int(link["in_use"]),
                          link["created_at"]))


def merge_faces(db: Database, face_id: str, into_id: str) -> dict[str, Any]:
    """`face_id` becomes a part of `into_id` everywhere: its links to chats, its development layers (target
    'face:<id>'), its «Слово и нити» records (who, to whom, who knows) and its members' flags move to `into_id`; the
    face row itself stays, marked merged, only for undo. Returns what undo needs (`unmerge_faces`)."""
    old_t, new_t = FACE_PREFIX + face_id, FACE_PREFIX + into_id
    gone, into = get_face(db, face_id), get_face(db, into_id)
    if gone is None or into is None:
        raise KeyError(face_id if gone is None else into_id)
    links = face_links(db, face_id=face_id) + face_links(db, face_id=into_id)
    texts = [r["id"] for r in db.fetchall(f"SELECT id FROM {TEXTS} WHERE target = ?", (old_t,))]
    pattern = f'%"{old_t}"%'
    thread_before = [_thread(r) for r in db.fetchall(
        f"SELECT {', '.join(THREAD_FIELDS)} FROM {THREADS} WHERE who = ? OR to_whom = ? OR knows LIKE ?",
        (old_t, old_t, pattern))]
    state_before = [{"id": r["id"], "knows": r["knows"]} for r in db.fetchall(
        f"SELECT id, knows FROM {THREAD_STATES} WHERE knows LIKE ?", (pattern,))]
    members = [{"chat_id": r["chat_id"], "remembers_all": r["remembers_all"], "no_layer": r["no_layer"]}
               for r in db.fetchall(f"SELECT chat_id, remembers_all, no_layer FROM {MEMBERS} WHERE character_id = ?",
                                    (old_t,))]

    def swap(items: list[str]) -> list[str]:
        out: list[str] = []
        for item in items:
            item = new_t if item == old_t else item
            if item not in out:
                out.append(item)
        return out

    with db.transaction() as conn:
        for link in links:
            if link["face_id"] == face_id:
                conn.execute(f"INSERT INTO {FACE_CHATS} (face_id, chat_id, first_message_id, in_use, created_at) "
                             "VALUES (?, ?, ?, ?, ?) ON CONFLICT(face_id, chat_id) DO NOTHING",
                             (into_id, link["chat_id"], link["first_message_id"], int(link["in_use"]),
                              link["created_at"]))
        conn.execute(f"DELETE FROM {FACE_CHATS} WHERE face_id = ?", (face_id,))
        conn.execute(f"UPDATE {TEXTS} SET target = ? WHERE target = ?", (new_t, old_t))
        for row in thread_before:
            conn.execute(f"UPDATE {THREADS} SET who = ?, to_whom = ?, knows = ? WHERE id = ?",
                         (new_t if row["who"] == old_t else row["who"],
                          new_t if row["to_whom"] == old_t else row["to_whom"],
                          json.dumps(swap(row["knows"]), ensure_ascii=False), row["id"]))
        for row in state_before:
            conn.execute(f"UPDATE {THREAD_STATES} SET knows = ? WHERE id = ?",
                         (json.dumps(swap(json.loads(row["knows"])), ensure_ascii=False), row["id"]))
        conn.execute(f"DELETE FROM {MEMBERS} WHERE character_id = ?", (old_t,))
        now = now_iso()
        aliases = [*into["aliases"]]
        for alias in [gone["name"], *gone["aliases"]]:
            if alias.casefold() != into["name"].casefold() and all(alias.casefold() != a.casefold() for a in aliases):
                aliases.append(alias)
        conn.execute(f"UPDATE {FACES} SET aliases = ?, favourite = ?, updated_at = ? WHERE id = ?",
                     (json.dumps(aliases, ensure_ascii=False), int(into["favourite"] or gone["favourite"]), now,
                      into_id))
        conn.execute(f"UPDATE {FACES} SET merged_into = ?, updated_at = ? WHERE id = ?", (into_id, now, face_id))
    return {"faces": [gone, into], "links": links, "texts": texts, "old": old_t, "threads": thread_before,
            "states": state_before, "members": members}


def unmerge_faces(db: Database, step: dict[str, Any]) -> None:
    """Undo of `merge_faces`: both faces, their links, the moved layers, records and flags as they were."""
    with db.transaction() as conn:
        for face in step["faces"]:
            conn.execute(f"DELETE FROM {FACE_CHATS} WHERE face_id = ?", (face["id"],))
        for text_id in step["texts"]:
            conn.execute(f"UPDATE {TEXTS} SET target = ? WHERE id = ?", (step["old"], text_id))
        for row in step["states"]:
            conn.execute(f"UPDATE {THREAD_STATES} SET knows = ? WHERE id = ?", (row["knows"], row["id"]))
        for row in step.get("members", []):
            conn.execute(f"INSERT OR REPLACE INTO {MEMBERS} (chat_id, character_id, remembers_all, no_layer) "
                         "VALUES (?, ?, ?, ?)", (row["chat_id"], step["old"], row["remembers_all"], row["no_layer"]))
    for face in step["faces"]:
        put_face(db, face)
    put_links(db, step["links"])
    for row in step["threads"]:
        put_thread(db, row)


def undo_face(db: Database, step: dict[str, Any]) -> None:
    """Reverse one hand edit of a face (chapters.undo): a merge, the version it wrote, the face as it was before."""
    if step.get("merge"):
        unmerge_faces(db, step["merge"])
        return
    if step.get("version"):
        delete_face_version(db, step["version"])
    if step.get("before"):
        put_face(db, step["before"])


def face_runs(db: Database, chat_id: str) -> dict[str, dict[str, Any]]:
    """chapter id -> {state, error, tries, failed_at} of the faces step."""
    return runs(db, FACE_RUNS, chat_id)


def put_face_run(db: Database, chat_id: str, chapter_id: str, state: str, error: str = "") -> None:
    put_run(db, FACE_RUNS, chat_id, chapter_id, state, error)
